package filewatch

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"io/fs"
	"log/slog"
	"os"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
)

const (
	HashOK          = "ok"
	HashSkippedSize = "skipped_size"
	HashUnavailable = "unavailable"
	// HashGone — файла уже нет или это каталог: события не будет.
	HashGone = "gone"
)

var errIsDirectory = errors.New("это каталог")

type HashResult struct {
	SHA256 string
	Size   int64
	Status string
	// Staged — копия содержимого снята для загрузки на сервер.
	Staged bool
	// StageSkip — почему копию не сняли (artifacts.Skip*); пусто, если сняли
	// или не пытались.
	StageSkip string
}

// Hasher считает SHA-256 файла потоком. Зависимости подменяются в тестах.
type Hasher struct {
	Open  func(path string) (io.ReadCloser, int64, error)
	Sleep func(time.Duration)
	Now   func() time.Time
}

func NewHasher() Hasher {
	return Hasher{Open: openFile, Sleep: time.Sleep, Now: time.Now}
}

// Hash открывает файл и считает хеш без копии содержимого.
func (h Hasher) Hash(path string, maxBytes int64, deadline time.Time) HashResult {
	return h.HashStaged(path, maxBytes, deadline, nil)
}

// HashStaged считает хеш и в том же проходе чтения отдаёт байты stager'у.
// Файл, который копируют прямо сейчас, часто занят: попытки повторяются с
// нарастающей паузой до deadline, затем событие уходит без хеша, а не
// откладывается навсегда. Файл больше maxBytes не читается. Копия не должна
// ломать хеширование: сбой записи отменяет копию, хеш остаётся.
func (h Hasher) HashStaged(path string, maxBytes int64, deadline time.Time, stager artifacts.Stager) HashResult {
	delay := 50 * time.Millisecond
	for {
		reader, size, err := h.Open(path)
		if err == nil {
			result, readErr := hashReader(reader, size, maxBytes, stager)
			reader.Close()
			if readErr == nil {
				return result
			}
			err = readErr
		}
		if errors.Is(err, os.ErrNotExist) || errors.Is(err, errIsDirectory) {
			return HashResult{Status: HashGone}
		}
		// Отказ в доступе не пройдёт от ожидания: повторы до конца окна только
		// держали бы цикл сборщика.
		if errors.Is(err, fs.ErrPermission) {
			return HashResult{Status: HashUnavailable}
		}
		if !h.Now().Add(delay).Before(deadline) {
			return HashResult{Status: HashUnavailable}
		}
		h.Sleep(delay)
		if delay < time.Second {
			delay *= 2
		}
	}
}

func hashReader(reader io.Reader, size, maxBytes int64, stager artifacts.Stager) (HashResult, error) {
	if size > maxBytes {
		return HashResult{Size: size, Status: HashSkippedSize}, nil
	}
	sum := sha256.New()
	var dst io.Writer = sum
	var tee *stageTee
	skip := ""
	if stager != nil {
		if sink, why := stager.Begin(size); sink != nil {
			tee = &stageTee{sink: sink}
			dst = io.MultiWriter(sum, tee)
		} else {
			skip = why
		}
	}

	read, err := io.Copy(dst, reader)
	if err != nil {
		if tee != nil {
			tee.abort()
		}
		return HashResult{}, err
	}
	result := HashResult{SHA256: hex.EncodeToString(sum.Sum(nil)), Size: read, Status: HashOK, StageSkip: skip}
	if tee != nil {
		result.Staged = tee.commit(result.SHA256)
	}
	return result, nil
}

// stageTee передаёт байты копии и никогда не возвращает ошибку: сбой копии
// не должен срывать подсчёт хеша.
type stageTee struct {
	sink   artifacts.Sink
	failed bool
}

func (t *stageTee) Write(p []byte) (int, error) {
	if !t.failed {
		if _, err := t.sink.Write(p); err != nil {
			t.failed = true
			t.sink.Abort()
		}
	}
	return len(p), nil
}

// abort отменяет копию, если сбой записи ещё не отменил её.
func (t *stageTee) abort() {
	if !t.failed {
		t.failed = true
		t.sink.Abort()
	}
}

func (t *stageTee) commit(sha string) bool {
	if t.failed {
		return false
	}
	if err := t.sink.Commit(sha); err != nil {
		slog.Warn("копия файла не сохранена", "error", err)
		return false
	}
	return true
}
