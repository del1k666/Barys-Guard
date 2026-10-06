// agent/internal/artifacts/crypto_test.go
package artifacts

import (
	"bytes"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"io"
	"testing"
)

var testKey = bytes.Repeat([]byte{7}, 32)

func seal(t *testing.T, key, data []byte) []byte {
	t.Helper()
	var out bytes.Buffer
	writer, err := newEncryptWriter(&out, key)
	if err != nil {
		t.Fatal(err)
	}
	// Мелкими порциями, как это делает io.Copy поверх файла.
	for rest := data; len(rest) > 0; {
		n := min(len(rest), 4096)
		if _, err := writer.Write(rest[:n]); err != nil {
			t.Fatal(err)
		}
		rest = rest[n:]
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return out.Bytes()
}

func unseal(key, raw []byte) ([]byte, error) {
	reader, err := newDecryptReader(bytes.NewReader(raw), key)
	if err != nil {
		return nil, err
	}
	return io.ReadAll(reader)
}

func TestRoundTripAtBlockBoundaries(t *testing.T) {
	for _, size := range []int{0, 1, blockSize - 1, blockSize, blockSize + 1, 2 * blockSize, 2*blockSize + 17} {
		data := make([]byte, size)
		rand.Read(data)

		got, err := unseal(testKey, seal(t, testKey, data))
		if err != nil {
			t.Fatalf("размер %d: %v", size, err)
		}
		if !bytes.Equal(got, data) {
			t.Fatalf("размер %d: данные не совпали", size)
		}
	}
}

func TestSealedDataHidesThePlaintext(t *testing.T) {
	marker := []byte("4111 1111 1111 1111 SECRET")
	raw := seal(t, testKey, bytes.Repeat(marker, 100))
	if bytes.Contains(raw, marker) {
		t.Fatal("открытый текст виден в зашифрованном файле")
	}
}

func TestEachCopyGetsItsOwnSalt(t *testing.T) {
	data := bytes.Repeat([]byte("a"), 1000)
	if bytes.Equal(seal(t, testKey, data), seal(t, testKey, data)) {
		t.Fatal("одинаковые данные дали одинаковый шифртекст: соль не случайна")
	}
}

func TestWrongKeyIsRejected(t *testing.T) {
	raw := seal(t, testKey, []byte("hello"))
	if _, err := unseal(bytes.Repeat([]byte{9}, 32), raw); !errors.Is(err, errCorrupt) {
		t.Fatalf("ошибка = %v, ожидалась errCorrupt", err)
	}
}

func TestFlippedByteIsDetected(t *testing.T) {
	raw := seal(t, testKey, bytes.Repeat([]byte("x"), 5000))
	raw[len(raw)/2] ^= 1
	if _, err := unseal(testKey, raw); !errors.Is(err, errCorrupt) {
		t.Fatalf("ошибка = %v, ожидалась errCorrupt", err)
	}
}

// Усечение ровно по границе блока: предыдущий блок шифровался как «не последний»,
// и признак последнего блока в связанных данных его выдаёт.
func TestTruncationAtABlockBoundaryIsDetected(t *testing.T) {
	data := make([]byte, 2*blockSize+5)
	rand.Read(data)
	raw := seal(t, testKey, data)

	const header = 4 + saltSize
	first := int(binary.BigEndian.Uint32(raw[header : header+4]))
	truncated := raw[:header+4+first]

	if _, err := unseal(testKey, truncated); !errors.Is(err, errCorrupt) {
		t.Fatalf("ошибка = %v, ожидалась errCorrupt", err)
	}
}

func TestWriterNeverHoldsMoreThanOneBlock(t *testing.T) {
	var out bytes.Buffer
	writer, err := newEncryptWriter(&out, testKey)
	if err != nil {
		t.Fatal(err)
	}
	chunk := make([]byte, 4096)
	for written := 0; written < 3*blockSize; written += len(chunk) {
		if _, err := writer.Write(chunk); err != nil {
			t.Fatal(err)
		}
		if cap(writer.buf) > blockSize {
			t.Fatalf("буфер вырос до %d байт", cap(writer.buf))
		}
	}
	writer.Close()
	if writer.plain != 3*blockSize {
		t.Fatalf("plain = %d, ожидалось %d", writer.plain, 3*blockSize)
	}
}

func TestDoubleCloseIsIdempotent(t *testing.T) {
	data := bytes.Repeat([]byte("abc"), 1000)
	var out bytes.Buffer
	writer, err := newEncryptWriter(&out, testKey)
	if err != nil {
		t.Fatal(err)
	}
	writer.Write(data)
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("повторный Close вернул %v", err)
	}
	got, err := unseal(testKey, out.Bytes())
	if err != nil || !bytes.Equal(got, data) {
		t.Fatalf("данные не восстановились: %v", err)
	}
}

func TestWriteAfterCloseFails(t *testing.T) {
	var out bytes.Buffer
	writer, err := newEncryptWriter(&out, testKey)
	if err != nil {
		t.Fatal(err)
	}
	writer.Write([]byte("hello"))
	writer.Close()
	before := append([]byte(nil), out.Bytes()...)

	if _, err := writer.Write([]byte("more")); err == nil {
		t.Fatal("Write после Close должен вернуть ошибку")
	}
	if !bytes.Equal(before, out.Bytes()) {
		t.Fatal("после Close в приёмник попали новые байты")
	}
	got, err := unseal(testKey, out.Bytes())
	if err != nil || string(got) != "hello" {
		t.Fatalf("got %q, err %v", got, err)
	}
}

// failingSink отказывает на failAt-й записи и считает всё, что к нему обратилось после.
type failingSink struct {
	calls, failAt, after int
}

func (s *failingSink) Write(p []byte) (int, error) {
	s.calls++
	if s.calls >= s.failAt {
		if s.calls > s.failAt {
			s.after++
		}
		return 0, errors.New("диск отказал")
	}
	return len(p), nil
}

func TestFailedFlushIsSticky(t *testing.T) {
	// Запись 1 — заголовок, 2 — длина первого блока, 3 — шифртекст.
	sink := &failingSink{failAt: 3}
	writer, err := newEncryptWriter(sink, testKey)
	if err != nil {
		t.Fatal(err)
	}
	chunk := make([]byte, blockSize)
	writer.Write(chunk) // буфер полон, сброса ещё не было
	if _, err := writer.Write([]byte{1}); err == nil {
		t.Fatal("ожидалась ошибка сброса блока")
	}
	calls := sink.calls
	if _, err := writer.Write([]byte{1}); err == nil {
		t.Fatal("Write после сбоя должен вернуть ошибку")
	}
	if err := writer.Close(); err == nil {
		t.Fatal("Close после сбоя должен вернуть ошибку")
	}
	if sink.calls != calls || sink.after != 0 {
		t.Fatalf("после сбоя в приёмник писали ещё: %d -> %d", calls, sink.calls)
	}
}

func TestBadMasterKeyLengthIsRejected(t *testing.T) {
	raw := seal(t, testKey, []byte("x"))
	for _, n := range []int{0, 31, 33} {
		key := make([]byte, n)
		if _, err := newEncryptWriter(io.Discard, key); err == nil {
			t.Fatalf("newEncryptWriter принял ключ длиной %d", n)
		}
		if _, err := newDecryptReader(bytes.NewReader(raw), key); err == nil {
			t.Fatalf("newDecryptReader принял ключ длиной %d", n)
		}
	}
	if _, err := newEncryptWriter(io.Discard, nil); err == nil {
		t.Fatal("nil-ключ принят")
	}
}
