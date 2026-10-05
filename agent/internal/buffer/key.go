package buffer

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strings"
	"time"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/platform"
)

const keySize = 32

// LoadOrCreateKey отдаёт ключ шифрования буфера. created = true означает,
// что прежнего ключа нет или он нечитаем, и вместо него создан новый:
// записи, зашифрованные старым ключом, прочитать уже нельзя.
//
// Ключ защищён так же, как ключ сертификата агента: права на файл
// выставляет и проверяет platform.Guard.
func LoadOrCreateKey(layout config.Layout, guard platform.Guard) ([]byte, bool, error) {
	path := layout.BufferKeyPath()

	if _, err := os.Stat(path); err == nil {
		if err := guard.VerifySecure(path); err != nil {
			return nil, false, err
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, false, err
		}
		key, err := hex.DecodeString(strings.TrimSpace(string(raw)))
		if err == nil && len(key) == keySize {
			return key, false, nil
		}
		// Испорченный файл равносилен потерянному ключу.
	} else if !errors.Is(err, fs.ErrNotExist) {
		return nil, false, err
	}

	key := make([]byte, keySize)
	if _, err := rand.Read(key); err != nil {
		return nil, false, fmt.Errorf("источник случайности: %w", err)
	}
	if err := config.WriteAtomic(path, []byte(hex.EncodeToString(key)), guard); err != nil {
		return nil, false, fmt.Errorf("запись ключа буфера: %w", err)
	}
	return key, true, nil
}

// OpenAt открывает буфер в рабочем каталоге агента. Второе значение true,
// если прежний буфер уничтожен из-за потери ключа: без ключа его содержимое
// не прочитать, а держать нечитаемый файл значит копить мусор на диске.
func OpenAt(layout config.Layout, guard platform.Guard, limits Limits) (*Buffer, bool, error) {
	key, created, err := LoadOrCreateKey(layout, guard)
	if err != nil {
		return nil, false, err
	}

	reset := false
	if created {
		if err := os.Remove(layout.BufferPath()); err == nil {
			reset = true
		} else if !errors.Is(err, fs.ErrNotExist) {
			return nil, false, err
		}
	}

	if err := guard.SecureDir(layout.Dir); err != nil {
		return nil, false, err
	}
	buf, err := Open(Options{
		Path:     layout.BufferPath(),
		Key:      key,
		MaxBytes: limits.MaxBytes,
		MaxAge:   limits.MaxAge,
		Now:      time.Now,
	})
	if err != nil {
		return nil, false, err
	}
	if err := guard.SecureFile(layout.BufferPath()); err != nil {
		buf.Close()
		return nil, false, err
	}
	return buf, reset, nil
}
