package buffer_test

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	bolt "go.etcd.io/bbolt"

	"github.com/barysguard/agent/internal/buffer"
	"github.com/barysguard/agent/internal/events"
)

var fixedNow = time.Date(2026, 10, 5, 10, 0, 0, 0, time.UTC)

func testKey(fill byte) []byte { return bytes.Repeat([]byte{fill}, 32) }

func open(t *testing.T, mutate func(*buffer.Options)) (*buffer.Buffer, buffer.Options) {
	t.Helper()
	options := buffer.Options{
		Path:     filepath.Join(t.TempDir(), "events.db"),
		Key:      testKey(1),
		MaxBytes: 1 << 30,
		MaxAge:   7 * 24 * time.Hour,
		Now:      func() time.Time { return fixedNow },
	}
	if mutate != nil {
		mutate(&options)
	}
	buf, err := buffer.Open(options)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { buf.Close() })
	return buf, options
}

// event строит событие с фиксированным временем: размер записи тогда не зависит
// от формата дробной части секунд, и тесты лимитов остаются детерминированными.
func event(t *testing.T, action, severity string) events.Envelope {
	t.Helper()
	env, err := events.NewEnvelope(events.ChannelAgent, action, severity, map[string]any{"detail": "x"})
	if err != nil {
		t.Fatal(err)
	}
	env.OccurredAt = fixedNow
	return env
}

func mustAppend(t *testing.T, buf *buffer.Buffer, env events.Envelope) {
	t.Helper()
	if err := buf.Append(env); err != nil {
		t.Fatalf("Append(%s): %v", env.Action, err)
	}
}

func actions(t *testing.T, batch buffer.Batch) []string {
	t.Helper()
	var out []string
	for _, line := range batch.Lines {
		s := string(line)
		i := strings.Index(s, `"action":"`) + len(`"action":"`)
		out = append(out, s[i:i+strings.Index(s[i:], `"`)])
	}
	return out
}

func TestBatchesComeOutInFIFOOrderAndAckRemovesThem(t *testing.T) {
	buf, _ := open(t, nil)
	for _, name := range []string{"a", "b", "c"} {
		mustAppend(t, buf, event(t, name, events.SeverityInfo))
	}

	batch, err := buf.NextBatch(2, 1<<20)
	if err != nil {
		t.Fatalf("NextBatch: %v", err)
	}
	if got := actions(t, batch); len(got) != 2 || got[0] != "a" || got[1] != "b" {
		t.Fatalf("первый пакет: %v", got)
	}

	// До Ack запись не удалена: обрыв связи не должен её терять.
	if count, _ := buf.Stats(); count != 3 {
		t.Fatalf("до Ack в буфере %d, ожидалось 3", count)
	}
	if err := buf.Ack(batch); err != nil {
		t.Fatalf("Ack: %v", err)
	}
	if count, _ := buf.Stats(); count != 1 {
		t.Fatalf("после Ack в буфере %d, ожидалась 1", count)
	}

	next, _ := buf.NextBatch(10, 1<<20)
	if got := actions(t, next); len(got) != 1 || got[0] != "c" {
		t.Fatalf("второй пакет: %v", got)
	}
}

func TestBatchRespectsByteLimitButAlwaysTakesOneEvent(t *testing.T) {
	buf, _ := open(t, nil)
	mustAppend(t, buf, event(t, "a", events.SeverityInfo))
	mustAppend(t, buf, event(t, "b", events.SeverityInfo))

	batch, _ := buf.NextBatch(10, 10) // меньше размера любого события

	if batch.Len() != 1 {
		t.Fatalf("в пакете %d событий, ожидалось ровно одно", batch.Len())
	}
}

func TestBatchNDJSONHasOneLinePerEvent(t *testing.T) {
	buf, _ := open(t, nil)
	mustAppend(t, buf, event(t, "a", events.SeverityInfo))
	mustAppend(t, buf, event(t, "b", events.SeverityInfo))

	batch, _ := buf.NextBatch(10, 1<<20)
	body := batch.NDJSON()

	if bytes.Count(body, []byte("\n")) != 2 || !bytes.HasSuffix(body, []byte("\n")) {
		t.Fatalf("тело: %q", body)
	}
	if batch.Bytes != len(body) {
		t.Fatalf("Bytes = %d, длина тела %d", batch.Bytes, len(body))
	}
}

func TestEventsSurviveReopen(t *testing.T) {
	buf, options := open(t, nil)
	mustAppend(t, buf, event(t, "persisted", events.SeverityInfo))
	buf.Close()

	again, err := buffer.Open(options)
	if err != nil {
		t.Fatalf("Open повторно: %v", err)
	}
	defer again.Close()

	if count, size := again.Stats(); count != 1 || size == 0 {
		t.Fatalf("после перезапуска count=%d bytes=%d", count, size)
	}
}

func TestRecordsAreEncryptedAtRest(t *testing.T) {
	buf, options := open(t, nil)
	env := event(t, "marker", events.SeverityInfo)
	env.Subject = map[string]any{"path": "СЕКРЕТНЫЙ-ДОКУМЕНТ-12345.docx"}
	mustAppend(t, buf, env)
	buf.Close()

	raw, err := os.ReadFile(options.Path)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte("СЕКРЕТНЫЙ")) || bytes.Contains(raw, []byte("marker")) {
		t.Fatal("содержимое события читается в файле буфера")
	}
}

func TestTamperedRecordIsDroppedAndCountedAsLost(t *testing.T) {
	buf, options := open(t, nil)
	mustAppend(t, buf, event(t, "good-1", events.SeverityInfo))
	mustAppend(t, buf, event(t, "bad", events.SeverityInfo))
	mustAppend(t, buf, event(t, "good-2", events.SeverityInfo))
	buf.Close()

	// Подмена одного байта шифротекста второй записи.
	db, err := bolt.Open(options.Path, 0o600, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Update(func(tx *bolt.Tx) error {
		bucket := tx.Bucket([]byte("events"))
		cursor := bucket.Cursor()
		cursor.First()
		key, value := cursor.Next()
		changed := append([]byte(nil), value...)
		changed[len(changed)-1] ^= 0xff
		return bucket.Put(append([]byte(nil), key...), changed)
	}); err != nil {
		t.Fatal(err)
	}
	db.Close()

	again, err := buffer.Open(options)
	if err != nil {
		t.Fatal(err)
	}
	defer again.Close()

	batch, err := again.NextBatch(10, 1<<20)
	if err != nil {
		t.Fatalf("NextBatch: %v", err)
	}
	if got := actions(t, batch); len(got) != 2 || got[0] != "good-1" || got[1] != "good-2" {
		t.Fatalf("после подмены: %v", got)
	}
	if lost := again.TakeLost(); lost != 1 {
		t.Fatalf("TakeLost = %d, ожидалась 1", lost)
	}
	if again.TakeLost() != 0 {
		t.Fatal("TakeLost не сбросил счётчик")
	}
}

func TestWrongKeyDropsEverythingInsteadOfBlocking(t *testing.T) {
	buf, options := open(t, nil)
	mustAppend(t, buf, event(t, "a", events.SeverityInfo))
	mustAppend(t, buf, event(t, "b", events.SeverityInfo))
	buf.Close()

	options.Key = testKey(2)
	other, err := buffer.Open(options)
	if err != nil {
		t.Fatal(err)
	}
	defer other.Close()

	batch, err := other.NextBatch(10, 1<<20)
	if err != nil || batch.Len() != 0 {
		t.Fatalf("batch=%d err=%v, ожидался пустой пакет", batch.Len(), err)
	}
	if other.TakeLost() != 2 {
		t.Fatal("потеряно не 2 события")
	}
}

// recordSize измеряет, сколько места занимает одно событие в буфере. Длина action
// меняет размер записи на единицы байт из сотен; запас size/2 в тестах лимитов
// эту разницу переживает.
func recordSize(t *testing.T) int64 {
	buf, _ := open(t, nil)
	mustAppend(t, buf, event(t, "m", events.SeverityInfo))
	_, size := buf.Stats()
	return size
}

func TestFullBufferEvictsOldestLowestSeverityFirst(t *testing.T) {
	size := recordSize(t)
	buf, _ := open(t, func(o *buffer.Options) { o.MaxBytes = 3*size + size/2 })
	mustAppend(t, buf, event(t, "old-info", events.SeverityInfo))
	mustAppend(t, buf, event(t, "high", events.SeverityHigh))
	mustAppend(t, buf, event(t, "new-info", events.SeverityInfo))

	mustAppend(t, buf, event(t, "critical", events.SeverityCritical))

	batch, _ := buf.NextBatch(10, 1<<20)
	got := actions(t, batch)
	if len(got) != 3 || got[0] != "high" || got[1] != "new-info" || got[2] != "critical" {
		t.Fatalf("осталось %v, ожидалось [high new-info critical]", got)
	}
	if buf.TakeLost() != 1 {
		t.Fatal("вытеснение не учтено как потеря")
	}
}

func TestCriticalEventsAreNeverEvicted(t *testing.T) {
	size := recordSize(t)
	buf, _ := open(t, func(o *buffer.Options) { o.MaxBytes = 2*size + size/2 })
	mustAppend(t, buf, event(t, "c1", events.SeverityCritical))
	mustAppend(t, buf, event(t, "c2", events.SeverityCritical))

	for _, severity := range []string{events.SeverityCritical, events.SeverityInfo} {
		if err := buf.Append(event(t, "overflow", severity)); !errors.Is(err, buffer.ErrFull) {
			t.Fatalf("Append(%s) = %v, ожидался ErrFull", severity, err)
		}
	}

	batch, _ := buf.NextBatch(10, 1<<20)
	if got := actions(t, batch); len(got) != 2 || got[0] != "c1" || got[1] != "c2" {
		t.Fatalf("critical потеряны: %v", got)
	}
	if count, _ := buf.Stats(); count != 2 {
		t.Fatalf("счётчик = %d после отказов", count)
	}
}

func TestLowSeverityNeverDisplacesMoreImportantEvents(t *testing.T) {
	size := recordSize(t)
	buf, _ := open(t, func(o *buffer.Options) { o.MaxBytes = 2*size + size/2 })
	mustAppend(t, buf, event(t, "h1", events.SeverityHigh))
	mustAppend(t, buf, event(t, "h2", events.SeverityHigh))

	if err := buf.Append(event(t, "noise", events.SeverityInfo)); !errors.Is(err, buffer.ErrFull) {
		t.Fatalf("Append(info) = %v, ожидался ErrFull", err)
	}
}

func TestOldEventsExpireExceptCritical(t *testing.T) {
	now := fixedNow
	buf, _ := open(t, func(o *buffer.Options) {
		o.MaxAge = time.Hour
		o.Now = func() time.Time { return now }
	})
	mustAppend(t, buf, event(t, "stale-info", events.SeverityInfo))
	mustAppend(t, buf, event(t, "stale-critical", events.SeverityCritical))

	now = fixedNow.Add(2 * time.Hour)
	mustAppend(t, buf, event(t, "fresh", events.SeverityInfo))

	batch, _ := buf.NextBatch(10, 1<<20)
	got := actions(t, batch)
	if len(got) != 2 || got[0] != "stale-critical" || got[1] != "fresh" {
		t.Fatalf("осталось %v", got)
	}
	if buf.TakeLost() != 1 {
		t.Fatal("истёкшее событие не учтено как потеря")
	}
}

func TestAckAfterConcurrentEvictionKeepsCountersConsistent(t *testing.T) {
	size := recordSize(t)
	buf, _ := open(t, func(o *buffer.Options) { o.MaxBytes = 2*size + size/2 })
	mustAppend(t, buf, event(t, "a", events.SeverityInfo))
	mustAppend(t, buf, event(t, "b", events.SeverityInfo))
	batch, _ := buf.NextBatch(10, 1<<20)

	// Пока пакет «в полёте», сборщик вытесняет его первое событие.
	mustAppend(t, buf, event(t, "c", events.SeverityCritical))
	if err := buf.Ack(batch); err != nil {
		t.Fatalf("Ack: %v", err)
	}

	count, bytesLeft := buf.Stats()
	if count != 1 || bytesLeft <= 0 {
		t.Fatalf("count=%d bytes=%d, ожидалась ровно одна запись", count, bytesLeft)
	}
}

// Потерянное событие низкой критичности не должно вытеснять настоящие события
// отчётом о потере: иначе поток info заменял бы события medium отчётами
// «потеряно 1», и гарантия «низкая критичность не вытесняет важную» не работала.
func TestLossReportDoesNotEvictMoreImportantEvents(t *testing.T) {
	size := recordSize(t)
	buf, _ := open(t, func(o *buffer.Options) { o.MaxBytes = 3*size + size/2 })
	for _, name := range []string{"m1", "m2", "m3"} {
		mustAppend(t, buf, event(t, name, events.SeverityMedium))
	}

	queue := events.NewQueue(10)
	for i := 0; i < 3; i++ {
		queue.Emit(event(t, "noise", events.SeverityInfo))
	}
	queue.Close()
	queue.Drain(buf.Append, buf.TakeLost)

	batch, _ := buf.NextBatch(10, 1<<20)
	got := actions(t, batch)
	if len(got) != 3 || got[0] != "m1" || got[1] != "m2" || got[2] != "m3" {
		t.Fatalf("осталось %v, ожидалось [m1 m2 m3]", got)
	}
}
