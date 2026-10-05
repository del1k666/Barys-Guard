// Package buffer — шифрованный offline-буфер событий (раздел 10 основной спеки).
//
// Событие сначала попадает сюда и только потом уходит на сервер: обратный
// порядок теряет события при обрыве связи. Запись удаляется после подтверждения
// сервера, не раньше.
package buffer

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	bolt "go.etcd.io/bbolt"

	"github.com/barysguard/agent/internal/events"
)

var bucketName = []byte("events")

// ErrFull — в буфере нет места, а вытеснять нечего: остались события, не менее
// важные, чем новое. Новое событие отбрасывается, а не вытесняет их.
var ErrFull = errors.New("буфер заполнен событиями не ниже по критичности")

var errCorrupt = errors.New("запись буфера повреждена")

// Запись: ранг (1) | время записи в мс (8) | nonce (12) | шифротекст.
// Ранг и время лежат открыто, чтобы вытеснение и истечение срока не расшифровывали
// сотни мегабайт; от подмены их защищает AAD — запись с изменённым заголовком
// не пройдёт проверку подлинности.
const (
	headerSize = 9
	nonceSize  = 12
)

type Options struct {
	Path     string
	Key      []byte
	MaxBytes int64
	MaxAge   time.Duration
	// Now подменяется в тестах; при nil берётся time.Now.
	Now func() time.Time
}

type Buffer struct {
	db   *bolt.DB
	aead cipher.AEAD
	opts Options

	mu    sync.Mutex
	count int
	bytes int64
	lost  uint64
}

// Batch — пакет записей, ожидающих подтверждения.
type Batch struct {
	keys [][]byte

	// Lines — строки JSON, готовые к отправке в NDJSON.
	Lines [][]byte
	// Bytes — размер тела NDJSON, включая переводы строк.
	Bytes int
}

func (b Batch) Len() int { return len(b.Lines) }

func (b Batch) NDJSON() []byte {
	var body bytes.Buffer
	for _, line := range b.Lines {
		body.Write(line)
		body.WriteByte('\n')
	}
	return body.Bytes()
}

func Open(opts Options) (*Buffer, error) {
	if len(opts.Key) != keySize {
		return nil, fmt.Errorf("ключ буфера: %d байт, нужно %d", len(opts.Key), keySize)
	}
	if opts.Now == nil {
		opts.Now = time.Now
	}

	block, err := aes.NewCipher(opts.Key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}

	db, err := bolt.Open(opts.Path, 0o600, &bolt.Options{Timeout: time.Second})
	if err != nil {
		return nil, fmt.Errorf("открытие буфера: %w", err)
	}

	b := &Buffer{db: db, aead: aead, opts: opts}
	err = db.Update(func(tx *bolt.Tx) error {
		bucket, err := tx.CreateBucketIfNotExists(bucketName)
		if err != nil {
			return err
		}
		return bucket.ForEach(func(_, value []byte) error {
			b.count++
			b.bytes += int64(len(value))
			return nil
		})
	})
	if err != nil {
		db.Close()
		return nil, err
	}
	return b, nil
}

func (b *Buffer) Close() error { return b.db.Close() }

func aad(key, header []byte) []byte {
	return append(append([]byte(nil), key...), header...)
}

func (b *Buffer) seal(key []byte, rank uint8, at time.Time, line []byte) ([]byte, error) {
	value := make([]byte, headerSize+nonceSize, headerSize+nonceSize+len(line)+b.aead.Overhead())
	value[0] = rank
	binary.BigEndian.PutUint64(value[1:headerSize], uint64(at.UnixMilli()))
	nonce := value[headerSize : headerSize+nonceSize]
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return b.aead.Seal(value, nonce, line, aad(key, value[:headerSize])), nil
}

func (b *Buffer) open(key, value []byte) ([]byte, error) {
	if len(value) < headerSize+nonceSize+b.aead.Overhead() {
		return nil, errCorrupt
	}
	nonce := value[headerSize : headerSize+nonceSize]
	return b.aead.Open(nil, nonce, value[headerSize+nonceSize:], aad(key, value[:headerSize]))
}

func appendedAt(value []byte) time.Time {
	return time.UnixMilli(int64(binary.BigEndian.Uint64(value[1:headerSize])))
}

func copyKey(key []byte) []byte { return append([]byte(nil), key...) }

func deleteKeys(bucket *bolt.Bucket, keys [][]byte) error {
	for _, key := range keys {
		if err := bucket.Delete(key); err != nil {
			return err
		}
	}
	return nil
}

// expire удаляет записи старше MaxAge, кроме critical. Записи упорядочены
// по времени, поэтому обход идёт до первой свежей некритичной.
func (b *Buffer) expire(bucket *bolt.Bucket, now time.Time) (int, int64, error) {
	if b.opts.MaxAge <= 0 {
		return 0, 0, nil
	}
	var keys [][]byte
	var freed int64
	cursor := bucket.Cursor()
	for key, value := cursor.First(); key != nil; key, value = cursor.Next() {
		if len(value) < headerSize {
			continue
		}
		if now.Sub(appendedAt(value)) <= b.opts.MaxAge {
			break
		}
		if value[0] >= events.CriticalRank {
			continue
		}
		keys = append(keys, copyKey(key))
		freed += int64(len(value))
	}
	return len(keys), freed, deleteKeys(bucket, keys)
}

// evict освобождает не меньше need байт, удаляя самые старые записи
// наименьшего ранга, но не выше maxRank и никогда не critical.
// Если освободить нужное нельзя, не удаляет ничего и возвращает false.
func evict(bucket *bolt.Bucket, need int64, maxRank uint8) (int, int64, bool, error) {
	var keys [][]byte
	var freed int64
	for rank := uint8(0); rank <= maxRank && rank < events.CriticalRank && freed < need; rank++ {
		cursor := bucket.Cursor()
		for key, value := cursor.First(); key != nil && freed < need; key, value = cursor.Next() {
			if len(value) < headerSize || value[0] != rank {
				continue
			}
			keys = append(keys, copyKey(key))
			freed += int64(len(value))
		}
	}
	if freed < need {
		return 0, 0, false, nil
	}
	return len(keys), freed, true, deleteKeys(bucket, keys)
}

// Append кладёт событие в буфер. При нехватке места вытесняет самые старые
// события низкой критичности; не помещающееся событие отбрасывается с ErrFull.
// Вытесненные и истёкшие записи учитываются в TakeLost.
func (b *Buffer) Append(env events.Envelope) error {
	line, err := env.MarshalLine()
	if err != nil {
		return err
	}
	rank := events.SeverityRank(env.SeverityHint)

	b.mu.Lock()
	defer b.mu.Unlock()
	now := b.opts.Now()

	var removedCount int
	var removedBytes, size int64

	err = b.db.Update(func(tx *bolt.Tx) error {
		bucket := tx.Bucket(bucketName)

		count, freed, err := b.expire(bucket, now)
		if err != nil {
			return err
		}
		removedCount, removedBytes = count, freed

		seq, err := bucket.NextSequence()
		if err != nil {
			return err
		}
		key := make([]byte, 8)
		binary.BigEndian.PutUint64(key, seq)

		value, err := b.seal(key, rank, now, line)
		if err != nil {
			return err
		}
		size = int64(len(value))

		if b.opts.MaxBytes > 0 {
			if need := b.bytes - removedBytes + size - b.opts.MaxBytes; need > 0 {
				count, freed, ok, err := evict(bucket, need, rank)
				if err != nil {
					return err
				}
				if !ok {
					return ErrFull
				}
				removedCount += count
				removedBytes += freed
			}
		}
		return bucket.Put(key, value)
	})
	if err != nil {
		return err
	}

	b.count += 1 - removedCount
	b.bytes += size - removedBytes
	b.lost += uint64(removedCount)
	return nil
}

// NextBatch отдаёт самые старые записи, не удаляя их. Запись, не прошедшая
// проверку подлинности, удаляется и считается потерянной: повреждённая
// запись в голове очереди иначе блокировала бы отправку всех следующих.
func (b *Buffer) NextBatch(maxEvents, maxBytes int) (Batch, error) {
	b.mu.Lock()
	defer b.mu.Unlock()

	var batch Batch
	var corrupt [][]byte
	var corruptBytes int64

	err := b.db.Update(func(tx *bolt.Tx) error {
		bucket := tx.Bucket(bucketName)
		cursor := bucket.Cursor()
		for key, value := cursor.First(); key != nil; key, value = cursor.Next() {
			if batch.Len() >= maxEvents {
				break
			}
			line, err := b.open(key, value)
			if err != nil {
				corrupt = append(corrupt, copyKey(key))
				corruptBytes += int64(len(value))
				continue
			}
			// Первое событие берётся всегда: иначе событие больше предела
			// застряло бы в голове очереди навсегда.
			if batch.Len() > 0 && batch.Bytes+len(line)+1 > maxBytes {
				break
			}
			batch.keys = append(batch.keys, copyKey(key))
			batch.Lines = append(batch.Lines, line)
			batch.Bytes += len(line) + 1
		}
		return deleteKeys(bucket, corrupt)
	})
	if err != nil {
		return Batch{}, err
	}

	if len(corrupt) > 0 {
		slog.Warn("записи буфера не прошли проверку и удалены", "count", len(corrupt))
		b.count -= len(corrupt)
		b.bytes -= corruptBytes
		b.lost += uint64(len(corrupt))
	}
	return batch, nil
}

// Ack удаляет подтверждённые записи. Размеры берутся из самой базы, а не из
// пакета: пока пакет был в пути, запись могло вытеснить, и повторное вычитание
// увело бы счётчики в минус.
func (b *Buffer) Ack(batch Batch) error {
	b.mu.Lock()
	defer b.mu.Unlock()

	var removed int
	var freed int64
	err := b.db.Update(func(tx *bolt.Tx) error {
		bucket := tx.Bucket(bucketName)
		removed, freed = 0, 0
		for _, key := range batch.keys {
			value := bucket.Get(key)
			if value == nil {
				continue
			}
			freed += int64(len(value))
			removed++
			if err := bucket.Delete(key); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		return err
	}
	b.count -= removed
	b.bytes -= freed
	return nil
}

func (b *Buffer) Stats() (int, int64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.count, b.bytes
}

// TakeLost возвращает число потерянных с прошлого вызова записей
// (вытеснение, истечение срока, повреждение) и обнуляет счётчик.
func (b *Buffer) TakeLost() uint64 {
	b.mu.Lock()
	defer b.mu.Unlock()
	lost := b.lost
	b.lost = 0
	return lost
}
