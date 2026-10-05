package filewatch

import (
	"bytes"
	"errors"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
)

type fakeSink struct {
	buf       bytes.Buffer
	committed string
	aborted   bool
	failWrite bool
}

func (s *fakeSink) Write(p []byte) (int, error) {
	if s.failWrite {
		return 0, errors.New("диск полон")
	}
	return s.buf.Write(p)
}
func (s *fakeSink) Commit(sha string) error { s.committed = sha; return nil }
func (s *fakeSink) Abort()                  { s.aborted = true }

type fakeStager struct {
	sink  *fakeSink
	skip  string
	calls []int64
}

func (f *fakeStager) Begin(size int64) (artifacts.Sink, string) {
	f.calls = append(f.calls, size)
	if f.skip != "" {
		return nil, f.skip
	}
	return f.sink, ""
}

func hasherOver(open func() (io.ReadCloser, int64, error)) Hasher {
	now := time.Date(2026, 10, 5, 12, 0, 0, 0, time.UTC)
	return Hasher{
		Open:  func(string) (io.ReadCloser, int64, error) { return open() },
		Sleep: func(time.Duration) {},
		Now:   func() time.Time { return now },
	}
}

func contentOf(s string) func() (io.ReadCloser, int64, error) {
	return func() (io.ReadCloser, int64, error) {
		return io.NopCloser(strings.NewReader(s)), int64(len(s)), nil
	}
}

func TestHashStagedFeedsTheSinkAndCommitsTheHash(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{}}
	h := hasherOver(contentOf("содержимое документа"))

	got := h.HashStaged("E:\\a.txt", 1<<20, time.Time{}, stager)

	if got.Status != HashOK || !got.Staged || got.StageSkip != "" {
		t.Fatalf("результат = %+v", got)
	}
	if stager.sink.committed != got.SHA256 {
		t.Fatalf("Commit(%q), хеш %q", stager.sink.committed, got.SHA256)
	}
	if stager.sink.buf.String() != "содержимое документа" {
		t.Fatal("в копию попали не те байты")
	}
}

func TestHashWithoutAStagerBehavesAsBefore(t *testing.T) {
	h := hasherOver(contentOf("abc"))

	got := h.Hash("E:\\a.txt", 1<<20, time.Time{})

	if got.Status != HashOK || got.Staged || got.StageSkip != "" {
		t.Fatalf("результат = %+v", got)
	}
}

func TestSkippedStagingIsReportedWithItsReason(t *testing.T) {
	stager := &fakeStager{skip: artifacts.SkipRate}
	h := hasherOver(contentOf("abc"))

	got := h.HashStaged("E:\\a.txt", 1<<20, time.Time{}, stager)

	if got.Status != HashOK || got.Staged || got.StageSkip != artifacts.SkipRate {
		t.Fatalf("результат = %+v", got)
	}
}

func TestSinkFailureAbortsStagingButNotTheHash(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{failWrite: true}}
	h := hasherOver(contentOf("abc"))

	got := h.HashStaged("E:\\a.txt", 1<<20, time.Time{}, stager)

	if got.Status != HashOK || got.SHA256 == "" {
		t.Fatalf("хеш не посчитан из-за сбоя копии: %+v", got)
	}
	if got.Staged || !stager.sink.aborted || stager.sink.committed != "" {
		t.Fatalf("копия не отменена: %+v, aborted=%v", got, stager.sink.aborted)
	}
}

type failingReader struct {
	data []byte
	err  error
}

func (r *failingReader) Read(p []byte) (int, error) {
	if len(r.data) == 0 {
		return 0, r.err
	}
	n := copy(p, r.data)
	r.data = r.data[n:]
	return n, nil
}
func (r *failingReader) Close() error { return nil }

func TestReadErrorAbortsTheSink(t *testing.T) {
	stager := &fakeStager{sink: &fakeSink{}}
	h := hasherOver(func() (io.ReadCloser, int64, error) {
		return &failingReader{data: []byte("начало"), err: errors.New("устройство извлечено")}, 20, nil
	})

	got := h.HashStaged("E:\\a.txt", 1<<20, time.Time{}, stager)

	if got.Status != HashUnavailable {
		t.Fatalf("статус = %q, ожидался %q", got.Status, HashUnavailable)
	}
	if !stager.sink.aborted || stager.sink.committed != "" {
		t.Fatal("копия оборванного чтения не отменена")
	}
}

// Файл вырос между stat и чтением (размер 100, байт 5000): реальный Store не
// даёт копии превысить MaxBytes, а хеш остаётся верным для прочитанного.
func TestGrowingFileAbortsStagingButNotTheHash(t *testing.T) {
	store, err := artifacts.NewStore(t.TempDir(), bytes.Repeat([]byte{3}, 32), time.Now)
	if err != nil {
		t.Fatal(err)
	}
	cfg := artifacts.DefaultConfig()
	cfg.MaxBytes = 1000
	store.SetConfig(cfg)

	grown := strings.Repeat("g", 5000)
	h := hasherOver(func() (io.ReadCloser, int64, error) {
		return io.NopCloser(strings.NewReader(grown)), 100, nil
	})

	got := h.HashStaged("E:\\a.txt", 1<<30, time.Time{}, store)

	if got.Status != HashOK || got.Size != 5000 {
		t.Fatalf("результат = %+v", got)
	}
	if got.Staged {
		t.Fatal("копия вышла за предел MaxBytes")
	}
	if entries, _ := store.List(); len(entries) != 0 {
		t.Fatalf("в каталоге копий %d записей после отмены", len(entries))
	}
}
