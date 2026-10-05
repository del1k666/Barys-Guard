// agent/internal/artifacts/crypto.go
package artifacts

import (
	"bufio"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"io"
)

const (
	magic     = "BGS1"
	saltSize  = 16
	blockSize = 1 << 20
	// Запас на тег GCM при проверке длины блока в чужом файле.
	tagSize = 16
)

var errCorrupt = errors.New("копия повреждена или ключ не подходит")

// deriveKey даёт каждой копии свой ключ: счётчик блока как nonce безопасен
// только пока ключ не повторяется между файлами.
func deriveKey(master, salt []byte) []byte {
	mac := hmac.New(sha256.New, master)
	mac.Write([]byte("barysguard-staging-v1"))
	mac.Write(salt)
	return mac.Sum(nil)
}

func newAEAD(master, salt []byte) (cipher.AEAD, error) {
	block, err := aes.NewCipher(deriveKey(master, salt))
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}

func nonce(index uint64) []byte {
	out := make([]byte, 12)
	binary.BigEndian.PutUint64(out[4:], index)
	return out
}

func aad(index uint64, last bool) []byte {
	out := make([]byte, 9)
	binary.BigEndian.PutUint64(out, index)
	if last {
		out[8] = 1
	}
	return out
}

// encryptWriter шифрует поток блоками. Последний блок должен быть помечен как
// последний, поэтому полный блок отправляется, только когда пришли ещё данные.
type encryptWriter struct {
	dst   io.Writer
	aead  cipher.AEAD
	buf   []byte
	index uint64
	// plain — сколько байт открытого текста принято.
	plain int64
}

func newEncryptWriter(dst io.Writer, master []byte) (*encryptWriter, error) {
	salt := make([]byte, saltSize)
	if _, err := rand.Read(salt); err != nil {
		return nil, err
	}
	aead, err := newAEAD(master, salt)
	if err != nil {
		return nil, err
	}
	if _, err := dst.Write(append([]byte(magic), salt...)); err != nil {
		return nil, err
	}
	return &encryptWriter{dst: dst, aead: aead, buf: make([]byte, 0, blockSize)}, nil
}

func (w *encryptWriter) Write(p []byte) (int, error) {
	total := len(p)
	for len(p) > 0 {
		if len(w.buf) == blockSize {
			if err := w.flush(false); err != nil {
				return 0, err
			}
		}
		n := min(len(p), blockSize-len(w.buf))
		w.buf = append(w.buf, p[:n]...)
		p = p[n:]
	}
	w.plain += int64(total)
	return total, nil
}

func (w *encryptWriter) flush(last bool) error {
	sealed := w.aead.Seal(nil, nonce(w.index), w.buf, aad(w.index, last))
	var length [4]byte
	binary.BigEndian.PutUint32(length[:], uint32(len(sealed)))
	if _, err := w.dst.Write(length[:]); err != nil {
		return err
	}
	if _, err := w.dst.Write(sealed); err != nil {
		return err
	}
	w.index++
	w.buf = w.buf[:0]
	return nil
}

// Close записывает последний блок. Он может быть пустым: пустой файл — это
// один пустой блок, а не отсутствие блоков.
func (w *encryptWriter) Close() error { return w.flush(true) }

type decryptReader struct {
	src   *bufio.Reader
	aead  cipher.AEAD
	index uint64
	plain []byte
	done  bool
}

func newDecryptReader(src io.Reader, master []byte) (io.Reader, error) {
	reader := bufio.NewReader(src)
	header := make([]byte, len(magic)+saltSize)
	if _, err := io.ReadFull(reader, header); err != nil || string(header[:len(magic)]) != magic {
		return nil, errCorrupt
	}
	aead, err := newAEAD(master, header[len(magic):])
	if err != nil {
		return nil, err
	}
	return &decryptReader{src: reader, aead: aead}, nil
}

func (r *decryptReader) Read(p []byte) (int, error) {
	for len(r.plain) == 0 {
		if r.done {
			return 0, io.EOF
		}
		if err := r.next(); err != nil {
			return 0, err
		}
	}
	n := copy(p, r.plain)
	r.plain = r.plain[n:]
	return n, nil
}

func (r *decryptReader) next() error {
	var length [4]byte
	if _, err := io.ReadFull(r.src, length[:]); err != nil {
		return errCorrupt
	}
	size := binary.BigEndian.Uint32(length[:])
	if size < tagSize || size > blockSize+tagSize {
		return errCorrupt
	}
	sealed := make([]byte, size)
	if _, err := io.ReadFull(r.src, sealed); err != nil {
		return errCorrupt
	}
	// Блок последний, если за ним ничего нет.
	_, peek := r.src.Peek(1)
	last := peek == io.EOF

	plain, err := r.aead.Open(nil, nonce(r.index), sealed, aad(r.index, last))
	if err != nil {
		return errCorrupt
	}
	r.index++
	r.plain = plain
	r.done = last
	return nil
}
