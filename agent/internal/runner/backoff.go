// Package runner отвечает за расписание работы агента: цикл heartbeat,
// паузы при сбоях и исполнение команд. Про HTTP он не знает ничего.
package runner

import (
	"math/rand"
	"time"
)

const (
	DefaultBackoffBase = time.Second
	DefaultBackoffMax  = 300 * time.Second

	// Доля разброса установившегося интервала опроса.
	intervalJitterPercent = 10
)

// Backoff выдаёт паузы по схеме полного джиттера: random(0, min(max, base·2^n)).
//
// Полного, а не половинного: при неполном джиттере переподключающийся флот
// сохраняет форму волны, и сервер, только что поднявшийся, ложится снова.
type Backoff struct {
	Base    time.Duration
	Max     time.Duration
	random  *rand.Rand
	attempt int
}

// NewBackoff принимает источник случайности параметром: иначе джиттер
// непроверяем — тест либо принимает любой результат, либо становится хлопающим.
func NewBackoff(base, max time.Duration, source rand.Source) *Backoff {
	return &Backoff{Base: base, Max: max, random: rand.New(source)}
}

func (b *Backoff) Next() time.Duration {
	ceiling := b.Base << b.attempt
	// Сдвиг переполняется быстрее, чем достигается разумный потолок.
	if ceiling <= 0 || ceiling > b.Max {
		ceiling = b.Max
	} else {
		b.attempt++
	}
	return time.Duration(b.random.Int63n(int64(ceiling) + 1))
}

func (b *Backoff) Reset() { b.attempt = 0 }

// JitterInterval разбрасывает установившийся интервал опроса на ±10%.
//
// Откат при сбоях эту задачу не решает: он включается только при ошибках,
// а синхронная волна возникает при совершенно штатной работе флота,
// развёрнутого за одну минуту.
func JitterInterval(interval time.Duration, random *rand.Rand) time.Duration {
	spread := int64(interval) * intervalJitterPercent / 100
	if spread <= 0 {
		return interval
	}
	return interval + time.Duration(random.Int63n(2*spread+1)-spread)
}
