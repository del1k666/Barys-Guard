package runner

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"reflect"
	"sync"

	"github.com/barysguard/agent/internal/buffer"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/transport"
)

// За один проход отправляется не больше стольких пакетов: накопившийся за
// неделю обрыва буфер не должен задерживать heartbeat и команды.
const MaxEventBatchesPerPass = 20

const (
	defaultBatchEvents = 500
	defaultBatchBytes  = 4 * 1024 * 1024
	queueCapacity      = 1024
)

// EventBuffer — то, что runner требует от буфера событий.
type EventBuffer interface {
	Append(events.Envelope) error
	NextBatch(maxEvents, maxBytes int) (buffer.Batch, error)
	Ack(buffer.Batch) error
	Stats() (count int, bytes int64)
	TakeLost() uint64
	Close() error
}

// Emit передаёт событие в очередь. Без буфера события не собираются.
func (a *Agent) Emit(env events.Envelope) {
	if a.options.Buffer == nil {
		return
	}
	a.queue.Emit(env)
}

// StartEvents запускает сборщиков и запись очереди в буфер. Возвращённая
// функция останавливает их в правильном порядке: сначала возвращаются
// сборщики (lifecycle успевает породить stop), затем закрывается очередь
// и дорабатывается её остаток.
func (a *Agent) StartEvents(ctx context.Context) (stop func()) {
	if a.options.Buffer == nil {
		return func() {}
	}
	buf := a.options.Buffer

	// Сборщики получают свой контекст: Run может вернуться раньше сигнала
	// (сервер отозвал сертификат), и сборщик, ждущий отмены, иначе не
	// остановился бы никогда, а main так и не дошёл бы до кода отзыва.
	collectorCtx, cancelCollectors := context.WithCancel(ctx)

	var collectors sync.WaitGroup
	for _, collector := range a.options.Collectors {
		collectors.Add(1)
		go func() {
			defer collectors.Done()
			if err := collector.Run(collectorCtx, a.queue.Emit); err != nil {
				slog.Error("сборщик остановился с ошибкой", "collector", collector.Name(), "error", err)
			}
		}()
	}

	if factory := a.options.CollectorFactory; factory != nil {
		document := a.state.Document
		a.collectorsDoc = document["collectors"]
		collectors.Add(1)
		go func() {
			defer collectors.Done()
			a.superviseCollectors(collectorCtx, factory, document)
		}()
	}

	drained := make(chan struct{})
	go func() {
		defer close(drained)
		a.queue.Drain(buf.Append, buf.TakeLost)
	}()

	return func() {
		cancelCollectors()
		collectors.Wait()
		a.queue.Close()
		<-drained
	}
}

// superviseCollectors держит группу сборщиков из фабрики и пересоздаёт её по
// новому документу. Остановка старой группы дожидается возврата всех её
// сборщиков: новая не должна работать параллельно со старой, иначе один том
// наблюдался бы дважды.
func (a *Agent) superviseCollectors(ctx context.Context, factory func(map[string]any) []events.Collector, document map[string]any) {
	var cancelGroup context.CancelFunc
	var group sync.WaitGroup

	startGroup := func(document map[string]any) {
		groupCtx, cancel := context.WithCancel(ctx)
		cancelGroup = cancel
		for _, collector := range factory(document) {
			group.Add(1)
			go func() {
				defer group.Done()
				if err := collector.Run(groupCtx, a.queue.Emit); err != nil {
					slog.Error("сборщик остановился с ошибкой", "collector", collector.Name(), "error", err)
				}
			}()
		}
	}
	stopGroup := func() {
		if cancelGroup != nil {
			cancelGroup()
			group.Wait()
		}
	}

	startGroup(document)
	for {
		select {
		case <-ctx.Done():
			stopGroup()
			return
		case next := <-a.reload:
			stopGroup()
			startGroup(next)
		}
	}
}

// notifyCollectorsReload просит пересоздать группу, если изменился раздел
// collectors. Смена других разделов группу не трогает: перезапуск опроса
// томов продублировал бы usb/mount для уже вставленных флешек.
func (a *Agent) notifyCollectorsReload() {
	if a.options.CollectorFactory == nil || a.options.Buffer == nil {
		return
	}
	section := a.state.Document["collectors"]
	if reflect.DeepEqual(section, a.collectorsDoc) {
		return
	}
	a.collectorsDoc = section
	// Нужен только самый свежий документ: устаревший ожидающий отбрасывается.
	select {
	case <-a.reload:
	default:
	}
	a.reload <- a.state.Document
}

// Close освобождает буфер событий.
func (a *Agent) Close() error {
	if a.options.Buffer == nil {
		return nil
	}
	return a.options.Buffer.Close()
}

func (a *Agent) emitConfigApplied(version int) {
	env, err := events.NewEnvelope(events.ChannelAgent, "config_applied", events.SeverityInfo, map[string]any{
		"component": "agent",
		"detail":    fmt.Sprintf("config_version %d", version),
	})
	if err != nil {
		slog.Error("событие смены конфигурации не создано", "error", err)
		return
	}
	a.Emit(env)
}

// batchLimits берёт пределы пакета из сохранённого документа конфигурации.
func (a *Agent) batchLimits() (int, int) {
	maxEvents, maxBytes := defaultBatchEvents, defaultBatchBytes
	section, _ := a.state.Document["transport"].(map[string]any)

	if value, ok := section["event_batch_max"].(float64); ok && value >= 1 {
		maxEvents = int(value)
	}
	if value, ok := section["event_batch_max_bytes"].(float64); ok && value >= 1 {
		maxBytes = int(value)
	}
	// Сервер ответил 413 на такой размер: пока агент не перезапущен, держимся ниже.
	if a.batchCap > 0 && a.batchCap < maxEvents {
		maxEvents = a.batchCap
	}
	return maxEvents, maxBytes
}

// flushEvents досылает накопленное. Ошибка отправки оставляет пакет в буфере:
// следующий heartbeat повторит попытку.
func (a *Agent) flushEvents(ctx context.Context) {
	buf := a.options.Buffer
	if buf == nil {
		return
	}

	for pass := 0; pass < MaxEventBatchesPerPass; pass++ {
		maxEvents, maxBytes := a.batchLimits()
		batch, err := buf.NextBatch(maxEvents, maxBytes)
		if err != nil {
			slog.Error("чтение буфера событий не удалось", "error", err)
			return
		}
		if batch.Len() == 0 {
			return
		}

		result, err := a.options.Client.SendEvents(ctx, batch.NDJSON())
		if err != nil {
			var statusErr *transport.StatusError
			if errors.As(err, &statusErr) && statusErr.Code == http.StatusRequestEntityTooLarge {
				if batch.Len() == 1 {
					// Одно событие, которое сервер не берёт по размеру, не станет
					// меньше от повторов и заблокировало бы очередь.
					slog.Warn("единичное событие слишком велико и удалено")
					if err := buf.Ack(batch); err != nil {
						slog.Error("подтверждение в буфере не удалось", "error", err)
						return
					}
					continue
				}
				a.batchCap = batch.Len() / 2
				continue
			}
			slog.Warn("события не отправлены", "count", batch.Len(), "error", err)
			return
		}

		for _, rejected := range result.Rejected {
			slog.Warn("сервер отклонил событие", "line", rejected.Line, "reason", rejected.Reason)
		}
		if err := buf.Ack(batch); err != nil {
			slog.Error("подтверждение в буфере не удалось", "error", err)
			return
		}
	}
}
