package events

import (
	"log/slog"
	"sync"
	"sync/atomic"
)

// Queue отделяет сборщиков от записи на диск: сборщик не должен ждать fsync
// буфера. Ёмкость конечна, при переполнении событие считается потерянным.
type Queue struct {
	ch      chan Envelope
	dropped atomic.Uint64

	mu     sync.RWMutex
	closed bool
}

func NewQueue(capacity int) *Queue {
	return &Queue{ch: make(chan Envelope, capacity)}
}

// Emit не блокируется. Блокировка на чтении защищает от отправки в закрытый
// канал: опоздавший сборщик получает потерянное событие, а не панику.
func (q *Queue) Emit(env Envelope) {
	q.mu.RLock()
	defer q.mu.RUnlock()
	if q.closed {
		q.dropped.Add(1)
		return
	}
	select {
	case q.ch <- env:
	default:
		q.dropped.Add(1)
	}
}

// Close останавливает приём; Drain доработает остаток и вернётся.
func (q *Queue) Close() {
	q.mu.Lock()
	defer q.mu.Unlock()
	if !q.closed {
		q.closed = true
		close(q.ch)
	}
}

// Drain передаёт события приёмнику, пока очередь не закрыта и не пуста.
// lost возвращает число событий, потерянных самим приёмником (вытеснение,
// истечение срока); может быть nil.
//
// Потери не молчат: после каждого события в приёмник добавляется
// agent/events_dropped с количеством. Отчёт имеет низшую критичность: с более
// высокой он вытеснял бы из полного буфера настоящие события ради сообщения
// о том, что потеряно ещё одно. Если и он не помещается, остаётся
// запись в журнале — повторно потери не пересчитываются, иначе при
// полном буфере получился бы бесконечный цикл отчётов.
func (q *Queue) Drain(appendEvent func(Envelope) error, lost func() uint64) {
	for env := range q.ch {
		if err := appendEvent(env); err != nil {
			slog.Warn("событие не записано в буфер", "event_id", env.EventID, "error", err)
			q.dropped.Add(1)
		}
		q.reportLoss(appendEvent, lost)
	}
	q.reportLoss(appendEvent, lost)
}

func (q *Queue) reportLoss(appendEvent func(Envelope) error, lost func() uint64) {
	count := q.dropped.Swap(0)
	if lost != nil {
		count += lost()
	}
	if count == 0 {
		return
	}
	report, err := NewEnvelope(ChannelAgent, "events_dropped", SeverityInfo, map[string]any{
		"component": "buffer",
		"detail":    "события потеряны: очередь или буфер переполнены, либо истёк срок",
		"count":     count,
	})
	if err != nil {
		slog.Error("отчёт о потерях не создан", "error", err)
		return
	}
	if err := appendEvent(report); err != nil {
		slog.Warn("отчёт о потерях не записан", "count", count, "error", err)
	}
}
