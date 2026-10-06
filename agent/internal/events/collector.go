package events

import "context"

// Collector — источник событий: файловая система, USB, буфер обмена и так далее.
// Новый перехват реализует этот интерфейс и подключается в main.
type Collector interface {
	Name() string
	// Run работает до отмены контекста. emit не блокируется: переполнение
	// очереди учитывается и сообщается событием agent/events_dropped.
	Run(ctx context.Context, emit func(Envelope)) error
}
