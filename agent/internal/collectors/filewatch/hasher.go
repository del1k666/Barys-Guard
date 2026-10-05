package filewatch

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"time"
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

func openFile(path string) (io.ReadCloser, int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	info, err := file.Stat()
	if err != nil {
		file.Close()
		return nil, 0, err
	}
	if info.IsDir() {
		file.Close()
		return nil, 0, errIsDirectory
	}
	return file, info.Size(), nil
}

// Hash открывает файл и считает хеш. Файл, который копируют прямо сейчас, часто
// занят: попытки повторяются с нарастающей паузой до deadline, затем событие
// уходит без хеша, а не откладывается навсегда. Файл больше maxBytes не читается.
func (h Hasher) Hash(path string, maxBytes int64, deadline time.Time) HashResult {
	delay := 50 * time.Millisecond
	for {
		reader, size, err := h.Open(path)
		if err == nil {
			result, readErr := hashReader(reader, size, maxBytes)
			reader.Close()
			if readErr == nil {
				return result
			}
			err = readErr
		}
		if errors.Is(err, os.ErrNotExist) || errors.Is(err, errIsDirectory) {
			return HashResult{Status: HashGone}
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

func hashReader(reader io.Reader, size, maxBytes int64) (HashResult, error) {
	if size > maxBytes {
		return HashResult{Size: size, Status: HashSkippedSize}, nil
	}
	sum := sha256.New()
	read, err := io.Copy(sum, reader)
	if err != nil {
		return HashResult{}, err
	}
	return HashResult{SHA256: hex.EncodeToString(sum.Sum(nil)), Size: read, Status: HashOK}, nil
}
