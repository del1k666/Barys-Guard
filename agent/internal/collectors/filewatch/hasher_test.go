package filewatch

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func memoryOpen(files map[string]string) func(string) (io.ReadCloser, int64, error) {
	return func(path string) (io.ReadCloser, int64, error) {
		content, ok := files[path]
		if !ok {
			return nil, 0, os.ErrNotExist
		}
		return io.NopCloser(strings.NewReader(content)), int64(len(content)), nil
	}
}

func TestHashOfAnOrdinaryFile(t *testing.T) {
	hasher := Hasher{Open: memoryOpen(map[string]string{`E:\a.txt`: "содержимое"}), Sleep: func(time.Duration) {}, Now: time.Now}

	result := hasher.Hash(`E:\a.txt`, 1<<20, time.Now().Add(time.Second))

	sum := sha256.Sum256([]byte("содержимое"))
	if result.Status != HashOK || result.SHA256 != hex.EncodeToString(sum[:]) || result.Size != int64(len("содержимое")) {
		t.Fatalf("результат: %+v", result)
	}
}

type readerFunc func([]byte) (int, error)

func (f readerFunc) Read(p []byte) (int, error) { return f(p) }
func (readerFunc) Close() error                 { return nil }

func TestFileLargerThanTheLimitIsSkippedWithoutReading(t *testing.T) {
	read := false
	hasher := Hasher{
		Open: func(string) (io.ReadCloser, int64, error) {
			return readerFunc(func([]byte) (int, error) { read = true; return 0, io.EOF }), 5 << 30, nil
		},
		Sleep: func(time.Duration) {}, Now: time.Now,
	}

	result := hasher.Hash(`E:\huge.iso`, 256<<20, time.Now().Add(time.Second))

	if result.Status != HashSkippedSize || result.Size != 5<<30 || read {
		t.Fatalf("результат: %+v, читали: %v", result, read)
	}
}

func TestMissingFileMeansGone(t *testing.T) {
	hasher := Hasher{Open: memoryOpen(nil), Sleep: func(time.Duration) {}, Now: time.Now}

	if got := hasher.Hash(`E:\none`, 1<<20, time.Now().Add(time.Second)); got.Status != HashGone {
		t.Fatalf("результат: %+v", got)
	}
}

func TestLockedFileIsRetriedThenReportedUnavailable(t *testing.T) {
	attempts := 0
	now := t0
	hasher := Hasher{
		Open: func(string) (io.ReadCloser, int64, error) {
			attempts++
			return nil, 0, errors.New("процесс не может получить доступ к файлу")
		},
		Sleep: func(d time.Duration) { now = now.Add(d) },
		Now:   func() time.Time { return now },
	}

	result := hasher.Hash(`C:\locked.pst`, 1<<20, t0.Add(2*time.Second))

	if result.Status != HashUnavailable {
		t.Fatalf("результат: %+v", result)
	}
	if attempts < 3 || attempts > 40 {
		t.Fatalf("попыток %d: повторы должны быть, но не бесконечные", attempts)
	}
}

func TestLockedFileThatOpensLaterIsHashed(t *testing.T) {
	attempts := 0
	hasher := Hasher{
		Open: func(string) (io.ReadCloser, int64, error) {
			attempts++
			if attempts < 3 {
				return nil, 0, errors.New("занят")
			}
			return io.NopCloser(strings.NewReader("ok")), 2, nil
		},
		Sleep: func(time.Duration) {}, Now: func() time.Time { return t0 },
	}

	if got := hasher.Hash(`C:\x`, 1<<20, t0.Add(time.Hour)); got.Status != HashOK {
		t.Fatalf("результат: %+v", got)
	}
}

func TestDefaultHasherRejectsDirectories(t *testing.T) {
	dir := t.TempDir()
	file := filepath.Join(dir, "f.txt")
	os.WriteFile(file, []byte("data"), 0o600)
	hasher := NewHasher()

	if got := hasher.Hash(dir, 1<<20, time.Now().Add(time.Second)); got.Status != HashGone {
		t.Fatalf("каталог: %+v", got)
	}
	if got := hasher.Hash(file, 1<<20, time.Now().Add(time.Second)); got.Status != HashOK {
		t.Fatalf("файл: %+v", got)
	}
}
