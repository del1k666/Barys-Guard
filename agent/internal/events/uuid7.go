package events

import (
	"crypto/rand"
	"fmt"
	"time"
)

// NewUUIDv7 строит UUID версии 7 (RFC 9562): 48 бит времени в миллисекундах,
// остальное случайное. Монотонность по времени даёт локальность вставки
// в индекс событий на сервере.
func NewUUIDv7(at time.Time) (string, error) {
	var b [16]byte
	millis := uint64(at.UnixMilli())
	for i := 0; i < 6; i++ {
		b[i] = byte(millis >> (8 * (5 - i)))
	}
	if _, err := rand.Read(b[6:]); err != nil {
		return "", fmt.Errorf("источник случайности: %w", err)
	}
	b[6] = b[6]&0x0f | 0x70
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:]), nil
}
