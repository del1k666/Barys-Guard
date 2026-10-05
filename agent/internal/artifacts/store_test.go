// agent/internal/artifacts/store_test.go
package artifacts

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type fakeClock struct{ now time.Time }

func (c *fakeClock) Now() time.Time { return c.now }

func newTestStore(t *testing.T, mutate func(*Config)) (*Store, *fakeClock) {
	t.Helper()
	clock := &fakeClock{now: time.Date(2026, 10, 5, 12, 0, 0, 0, time.UTC)}
	store, err := NewStore(t.TempDir(), testKey, clock.Now)
	if err != nil {
		t.Fatal(err)
	}
	cfg := DefaultConfig()
	if mutate != nil {
		mutate(&cfg)
	}
	store.SetConfig(cfg)
	return store, clock
}

func shaOf(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// stage кладёт данные в каталог копий и возвращает хеш.
func stage(t *testing.T, store *Store, data []byte) string {
	t.Helper()
	sink, skip := store.Begin(int64(len(data)))
	if sink == nil {
		t.Fatalf("копия не начата: %q", skip)
	}
	if _, err := sink.Write(data); err != nil {
		t.Fatal(err)
	}
	sha := shaOf(data)
	if err := sink.Commit(sha); err != nil {
		t.Fatal(err)
	}
	return sha
}

func age(t *testing.T, store *Store, sha string, size int, at time.Time) {
	t.Helper()
	path := filepath.Join(store.dir, fmt.Sprintf("%s-%d.enc", sha, size))
	if err := os.Chtimes(path, at, at); err != nil {
		t.Fatal(err)
	}
}

func TestStagedCopyIsListedAndReadable(t *testing.T) {
	store, _ := newTestStore(t, nil)
	data := bytes.Repeat([]byte("отчёт "), 500)
	sha := stage(t, store, data)

	entries, err := store.List()
	if err != nil || len(entries) != 1 {
		t.Fatalf("List = %+v, %v", entries, err)
	}
	if entries[0].SHA256 != sha || entries[0].Size != int64(len(data)) {
		t.Fatalf("запись = %+v", entries[0])
	}

	reader, err := store.Open(entries[0])
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	got, err := io.ReadAll(reader)
	if err != nil || !bytes.Equal(got, data) {
		t.Fatalf("прочитано %d байт, ошибка %v", len(got), err)
	}

	select {
	case <-store.Staged():
	default:
		t.Fatal("воркер не получил сигнала о новой копии")
	}
}

func TestSecondCopyOfTheSameFileReplacesTheFirst(t *testing.T) {
	store, _ := newTestStore(t, nil)
	data := bytes.Repeat([]byte("x"), 2000)
	stage(t, store, data)
	stage(t, store, data)

	if entries, _ := store.List(); len(entries) != 1 {
		t.Fatalf("записей %d, ожидалась одна", len(entries))
	}
}

func TestAbortLeavesNothingBehind(t *testing.T) {
	store, _ := newTestStore(t, nil)
	sink, _ := store.Begin(10)
	sink.Write([]byte("0123456789"))
	sink.Abort()

	files, _ := os.ReadDir(store.dir)
	if len(files) != 0 {
		t.Fatalf("после Abort в каталоге %d файлов", len(files))
	}
}

func TestDisabledAndOversizedFilesAreNotStaged(t *testing.T) {
	store, _ := newTestStore(t, func(c *Config) { c.MaxBytes = 100 })
	if sink, skip := store.Begin(101); sink != nil || skip != SkipSize {
		t.Fatalf("крупный файл: sink=%v skip=%q", sink, skip)
	}

	store.SetConfig(Config{Enabled: false, MaxBytes: 100, StagingMaxBytes: 1 << 20,
		UploadBytesPerSecond: 1, StageBytesPerMinute: 1 << 20})
	if sink, skip := store.Begin(1); sink != nil || skip != SkipDisabled {
		t.Fatalf("выключено: sink=%v skip=%q", sink, skip)
	}
}

func TestMinuteBudgetSkipsTheExcessAndCountsIt(t *testing.T) {
	store, clock := newTestStore(t, func(c *Config) { c.StageBytesPerMinute = 1500 })
	stage(t, store, bytes.Repeat([]byte("a"), 1000))

	if sink, skip := store.Begin(1000); sink != nil || skip != SkipRate {
		t.Fatalf("сверх бюджета: sink=%v skip=%q", sink, skip)
	}
	if got := store.TakeDropped(); got != 1 {
		t.Fatalf("TakeDropped = %d, ожидалось 1", got)
	}
	if got := store.TakeDropped(); got != 0 {
		t.Fatalf("повторный TakeDropped = %d, счётчик должен сбрасываться", got)
	}

	clock.now = clock.now.Add(61 * time.Second)
	if sink, skip := store.Begin(1000); sink == nil {
		t.Fatalf("новое окно: копия не начата (%q)", skip)
	} else {
		sink.Abort()
	}
}

func TestOldestCopiesAreEvictedWhenTheDirectoryIsFull(t *testing.T) {
	store, _ := newTestStore(t, func(c *Config) { c.StagingMaxBytes = 2500 })
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)

	a := bytes.Repeat([]byte("a"), 1000)
	b := bytes.Repeat([]byte("b"), 1000)
	c := bytes.Repeat([]byte("c"), 1000)
	stage(t, store, a)
	age(t, store, shaOf(a), len(a), base)
	stage(t, store, b)
	age(t, store, shaOf(b), len(b), base.Add(time.Hour))
	stage(t, store, c)

	entries, _ := store.List()
	if len(entries) != 2 {
		t.Fatalf("записей %d, ожидалось 2 после вытеснения", len(entries))
	}
	for _, e := range entries {
		if e.SHA256 == shaOf(a) {
			t.Fatal("самая старая копия не вытеснена")
		}
	}
	if got := store.TakeDropped(); got != 1 {
		t.Fatalf("вытеснено %d, ожидалось 1", got)
	}
}

// Файл вырос между stat и чтением: копия не должна превысить предел.
func TestWritePastTheLimitFails(t *testing.T) {
	store, _ := newTestStore(t, func(c *Config) { c.MaxBytes = 1000 })
	sink, _ := store.Begin(10)
	defer sink.Abort()

	if _, err := sink.Write(make([]byte, 2000)); err == nil {
		t.Fatal("запись сверх предела должна завершиться ошибкой")
	}
}

func TestCommitRejectsAHashThatCouldEscapeTheDirectory(t *testing.T) {
	store, _ := newTestStore(t, nil)
	sink, _ := store.Begin(3)
	sink.Write([]byte("abc"))

	if err := sink.Commit("../../evil"); err == nil {
		t.Fatal("недопустимый хеш принят")
	}
	if files, _ := os.ReadDir(store.dir); len(files) != 0 {
		t.Fatalf("после отказа в каталоге %d файлов", len(files))
	}
}

func TestLeftoverTemporaryFilesAreRemovedAtStart(t *testing.T) {
	dir := t.TempDir()
	leftover := filepath.Join(dir, "123.tmp")
	os.WriteFile(leftover, []byte("обрыв"), 0o600)

	if _, err := NewStore(dir, testKey, time.Now); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(leftover); !os.IsNotExist(err) {
		t.Fatal("временный файл прошлого запуска остался")
	}
}

// После ошибки записи копия считается испорченной: ни последующая запись, ни
// Commit не должны опубликовать файл с пробелом под хешем полного содержимого.
func TestFailedWriteIsStickyAndCommitDoesNotPublish(t *testing.T) {
	store, _ := newTestStore(t, func(c *Config) { c.MaxBytes = 1000 })
	sink, _ := store.Begin(10)

	if _, err := sink.Write(make([]byte, 2000)); err == nil {
		t.Fatal("запись сверх предела должна завершиться ошибкой")
	}
	if _, err := sink.Write([]byte("short")); err == nil {
		t.Fatal("запись после сбоя должна завершаться ошибкой")
	}
	if err := sink.Commit(shaOf([]byte("full"))); err == nil {
		t.Fatal("Commit после сбоя не должен публиковать копию")
	}
	if files, _ := os.ReadDir(store.dir); len(files) != 0 {
		t.Fatalf("в каталоге %d файлов, ожидалось 0", len(files))
	}
}

func TestWriteAfterAbortFails(t *testing.T) {
	store, _ := newTestStore(t, nil)
	sink, _ := store.Begin(3)
	sink.Abort()
	if _, err := sink.Write([]byte("abc")); err == nil {
		t.Fatal("запись после Abort должна завершаться ошибкой")
	}
}

// Файл занят (на Windows — воркером при загрузке): его нельзя считать
// вытесненным, вытеснение идёт дальше к следующей копии.
func TestEvictionSkipsAnEntryThatCannotBeRemoved(t *testing.T) {
	store, _ := newTestStore(t, func(c *Config) { c.StagingMaxBytes = 2500 })
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)

	a := bytes.Repeat([]byte("a"), 1000)
	b := bytes.Repeat([]byte("b"), 1000)
	c := bytes.Repeat([]byte("c"), 1000)
	stage(t, store, a)
	age(t, store, shaOf(a), len(a), base)
	stage(t, store, b)
	age(t, store, shaOf(b), len(b), base.Add(time.Hour))

	busy := fmt.Sprintf("%s-%d.enc", shaOf(a), len(a))
	store.remove = func(path string) error {
		if filepath.Base(path) == busy {
			return os.ErrPermission
		}
		return os.Remove(path)
	}
	stage(t, store, c)

	names := map[string]bool{}
	entries, _ := store.List()
	for _, e := range entries {
		names[e.SHA256] = true
	}
	if !names[shaOf(a)] || names[shaOf(b)] || !names[shaOf(c)] {
		t.Fatalf("после вытеснения остались %v, ожидались a и c", names)
	}
	if got := store.TakeDropped(); got != 1 {
		t.Fatalf("вытеснено %d, ожидалось 1: занятая копия не считается", got)
	}
}
