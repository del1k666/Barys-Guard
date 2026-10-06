package filewatch

import "time"

// Limiter — окно в одну секунду: массовые изменения (распаковка архива)
// не должны ни остановить сборщик, ни залить очередь событий.
type Limiter struct {
	perSecond   int
	windowStart time.Time
	used        int
}

func NewLimiter(perSecond int) *Limiter { return &Limiter{perSecond: perSecond} }

func (l *Limiter) Allow(now time.Time) bool {
	if now.Sub(l.windowStart) >= time.Second {
		l.windowStart, l.used = now, 0
	}
	if l.used >= l.perSecond {
		return false
	}
	l.used++
	return true
}
