package netupload

import (
	"context"
	"net/netip"
	"time"
)

type Kind int

const (
	// KindRead — процесс начал читать файл (Path — путь в формате DOS).
	KindRead Kind = iota + 1
	// KindSend — процесс отправил данные на Addr (Bytes).
	KindSend
	// KindDNS — ответ DNS: имена Names ведут на адреса Addrs.
	KindDNS
)

// Event — событие источника. Заполнены только поля своего вида.
type Event struct {
	Kind  Kind
	PID   uint32
	Path  string
	Addr  netip.Addr
	Bytes uint64
	Names []string
	Addrs []netip.Addr
	TTL   time.Duration
	At    time.Time
}

// Source поставляет события чтения файлов, сетевых отправок и ответов DNS.
// Run работает до отмены контекста; sink не блокируется. Ошибка Run означает,
// что источник недоступен (нет прав, нет ETW): сборщик сообщит об этом событием.
type Source interface {
	Run(ctx context.Context, sink func(Event)) error
}
