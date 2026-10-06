// Package volumes следит за подключёнными томами: сборщики usb и filewatch
// получают одни и те же изменения от одного опроса.
package volumes

import (
	"context"
	"log/slog"
	"sort"
	"sync"
	"time"

	"github.com/barysguard/agent/internal/events"
)

const (
	TypeFixed     = "fixed"
	TypeRemovable = "removable"
	TypeNetwork   = "network"
	TypeUnknown   = "unknown"

	BusUSB     = "usb"
	BusOther   = "other"
	BusUnknown = "unknown"
)

// subscriberBuffer хватает на повтор всех букв алфавита плюс запас на пачку изменений.
const subscriberBuffer = 256

type Volume struct {
	DriveLetter  string // "E:"
	Serial       string // серийный номер тома, hex
	Label, FS    string
	SizeBytes    int64
	Type         string
	Bus          string
	Vendor       string
	Product      string
	DeviceSerial string
}

// Key различает тома по паре «буква и серийный номер»: другая флешка
// в той же букве — это другой том.
func (v Volume) Key() string { return v.DriveLetter + "|" + v.Serial }

type Provider interface {
	Snapshot() ([]Volume, error)
}

type Change struct {
	Mounted bool
	Volume  Volume
}

// ClassifyType сводит тип диска Windows и шину к типу тома события.
// Внешним считается том на шине USB: внешние жёсткие диски Windows
// показывает как фиксированные, и по одному DRIVE_REMOVABLE их не поймать.
func ClassifyType(driveType, bus string) string {
	switch driveType {
	case "remote":
		return TypeNetwork
	case "removable":
		return TypeRemovable
	case "fixed":
		if bus == BusUSB {
			return TypeRemovable
		}
		return TypeFixed
	}
	return TypeUnknown
}

// diff отдаёт сначала извлечения, потом подключения: смена носителя в той же
// букве должна читаться как unmount и затем mount.
func diff(previous, current map[string]Volume) []Change {
	var out []Change
	for key, volume := range previous {
		if _, ok := current[key]; !ok {
			out = append(out, Change{Mounted: false, Volume: volume})
		}
	}
	for key, volume := range current {
		if _, ok := previous[key]; !ok {
			out = append(out, Change{Mounted: true, Volume: volume})
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Mounted != out[j].Mounted {
			return !out[i].Mounted
		}
		return out[i].Volume.Key() < out[j].Volume.Key()
	})
	return out
}

// Hub опрашивает провайдера и рассылает изменения подписчикам. Реализует
// events.Collector, чтобы запускаться вместе с остальными сборщиками;
// сам событий не порождает.
type Hub struct {
	provider Provider
	interval time.Duration

	mu    sync.Mutex
	known map[string]Volume
	subs  map[int]chan Change
	next  int
}

func NewHub(provider Provider, interval time.Duration) *Hub {
	return &Hub{provider: provider, interval: interval, known: map[string]Volume{}, subs: map[int]chan Change{}}
}

func (h *Hub) Name() string { return "volumes" }

// Subscribe подписывает на изменения. Подписчик сразу получает подключения
// для уже известных томов, поэтому порядок запуска сборщиков не важен.
func (h *Hub) Subscribe() (<-chan Change, func()) {
	h.mu.Lock()
	defer h.mu.Unlock()

	channel := make(chan Change, subscriberBuffer)
	id := h.next
	h.next++
	h.subs[id] = channel
	for _, volume := range sortedVolumes(h.known) {
		channel <- Change{Mounted: true, Volume: volume}
	}

	cancel := func() {
		h.mu.Lock()
		defer h.mu.Unlock()
		if existing, ok := h.subs[id]; ok {
			delete(h.subs, id)
			close(existing)
		}
	}
	return channel, cancel
}

func (h *Hub) Current() []Volume {
	h.mu.Lock()
	defer h.mu.Unlock()
	return sortedVolumes(h.known)
}

func sortedVolumes(known map[string]Volume) []Volume {
	out := make([]Volume, 0, len(known))
	for _, volume := range known {
		out = append(out, volume)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Key() < out[j].Key() })
	return out
}

// Poll выполняет один цикл опроса. Сбой снимка оставляет известные тома
// нетронутыми: иначе временная ошибка выглядела бы как извлечение всех носителей.
func (h *Hub) Poll() error {
	snapshot, err := h.provider.Snapshot()
	if err != nil {
		return err
	}
	current := make(map[string]Volume, len(snapshot))
	for _, volume := range snapshot {
		current[volume.Key()] = volume
	}

	h.mu.Lock()
	defer h.mu.Unlock()
	changes := diff(h.known, current)
	h.known = current
	for _, change := range changes {
		for id, channel := range h.subs {
			select {
			case channel <- change:
			default:
				// Медленный подписчик не должен останавливать остальных.
				slog.Warn("подписчик томов не успевает, изменение пропущено", "subscriber", id, "volume", change.Volume.Key())
			}
		}
	}
	return nil
}

func (h *Hub) Run(ctx context.Context, _ func(events.Envelope)) error {
	if err := h.Poll(); err != nil {
		slog.Warn("опрос томов не удался", "error", err)
	}
	if h.interval <= 0 {
		<-ctx.Done()
		return nil
	}
	ticker := time.NewTicker(h.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			if err := h.Poll(); err != nil {
				slog.Warn("опрос томов не удался", "error", err)
			}
		}
	}
}
