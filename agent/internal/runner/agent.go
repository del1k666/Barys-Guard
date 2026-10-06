package runner

import (
	"context"
	"crypto/x509"
	"errors"
	"fmt"
	"log/slog"
	"math/rand"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/keystore"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/transport"
)

// ErrRevoked означает, что сервер отказал в обслуживании: сертификат отозван
// либо не признан. Повторять запрос бессмысленно.
var ErrRevoked = errors.New("сервер отказал в обслуживании")

// Дальше сотни неотданных результатов копить незачем: команда, чей результат
// не удалось отдать за столько циклов, на сервере уже истекла.
const MaxPendingResults = 100

// Интервал, применяемый когда сервер прислал бессмысленное значение.
const fallbackInterval = 30 * time.Second

type pendingResult struct {
	commandID string
	body      transport.CommandResultRequest
}

// ArtifactWorker — загрузка на сервер копий файлов с внешних томов.
type ArtifactWorker interface {
	Run(ctx context.Context, emit func(events.Envelope))
	SetConfig(artifacts.Config)
}

type Options struct {
	ServerURL    string
	AgentVersion string
	Layout       config.Layout
	Guard        platform.Guard
	Client       *transport.Client
	Random       rand.Source
	// Now подменяется в тестах продления; при nil берётся time.Now.
	Now func() time.Time

	// Buffer — шифрованный offline-буфер. При nil события не собираются
	// и не отправляются.
	Buffer EventBuffer
	// Collectors запускаются в Run и в StartEvents.
	Collectors []events.Collector
	// CollectorFactory строит сборщики, зависящие от конфигурации. Группа
	// запускается при StartEvents и пересоздаётся, когда меняется раздел
	// collectors документа. Сборщики из Collectors от конфигурации не зависят
	// и не перезапускаются.
	CollectorFactory func(document map[string]any) []events.Collector
	// Artifacts отправляет копии файлов на сервер. nil — загрузки нет.
	// Запускается вместе со сборщиками и применяет раздел collectors.artifact.
	Artifacts ArtifactWorker
}

type Agent struct {
	options       Options
	state         config.State
	backoff       *Backoff
	random        *rand.Rand
	dispatcher    Dispatcher
	pending       []pendingResult
	queue         *events.Queue
	reload        chan map[string]any
	collectorsDoc any
	batchCap      int // верхний предел пакета после ответа 413

	startedAt       time.Time
	lastHeartbeatAt time.Time
	certNotAfter    time.Time
}

func New(options Options) (*Agent, error) {
	if options.Client == nil {
		return nil, errors.New("клиент транспорта не задан")
	}
	if options.Random == nil {
		options.Random = rand.NewSource(time.Now().UnixNano())
	}
	if options.Now == nil {
		options.Now = time.Now
	}

	state, err := config.LoadState(options.Layout)
	if err != nil {
		return nil, fmt.Errorf("чтение состояния: %w", err)
	}

	agent := &Agent{
		options:   options,
		state:     state,
		backoff:   NewBackoff(DefaultBackoffBase, DefaultBackoffMax, options.Random),
		random:    rand.New(options.Random),
		startedAt: options.Now(),
		queue:     events.NewQueue(queueCapacity),
		reload:    make(chan map[string]any, 1),
	}
	// Диспетчер замыкается на агента: обе функции обращаются к его состоянию.
	agent.dispatcher = NewDispatcher(agent.refreshConfig, agent.diagnostics)
	return agent, nil
}

func (a *Agent) diagnostics() Diagnostics {
	return Diagnostics{
		AgentVersion:    a.options.AgentVersion,
		StartedAt:       a.startedAt,
		CertNotAfter:    a.certNotAfter,
		LastHeartbeatAt: a.lastHeartbeatAt,
		ConfigVersion:   a.state.ConfigVersion,
	}
}

// refreshConfig забирает документ, игнорируя сохранённую версию.
func (a *Agent) refreshConfig(ctx context.Context) (int, error) {
	response, _, err := a.options.Client.Config(ctx, "")
	if err != nil {
		return 0, err
	}
	a.state.ConfigVersion = response.Version
	a.state.Document = response.Document
	if err := config.SaveState(a.options.Layout, a.state, a.options.Guard); err != nil {
		return 0, err
	}
	a.emitConfigApplied(response.Version)
	a.applyArtifactConfig()
	a.notifyCollectorsReload()
	return response.Version, nil
}

// syncConfig забирает документ, только если версия разошлась.
func (a *Agent) syncConfig(ctx context.Context, serverVersion int) error {
	if serverVersion == a.state.ConfigVersion && a.state.Document != nil {
		return nil
	}

	etag := fmt.Sprintf("%q", a.state.ConfigVersion)
	response, notModified, err := a.options.Client.Config(ctx, etag)
	if err != nil {
		return err
	}
	if notModified {
		return nil
	}

	a.state.ConfigVersion = response.Version
	a.state.Document = response.Document
	if err := config.SaveState(a.options.Layout, a.state, a.options.Guard); err != nil {
		return err
	}
	a.emitConfigApplied(response.Version)
	a.applyArtifactConfig()
	a.notifyCollectorsReload()
	return nil
}

// flushPending досылает результаты, не ушедшие в прошлые проходы.
func (a *Agent) flushPending(ctx context.Context) {
	remaining := a.pending[:0]
	for _, item := range a.pending {
		if err := a.options.Client.CommandResult(ctx, item.commandID, item.body); err != nil {
			slog.Warn("результат команды не отправлен", "command_id", item.commandID, "error", err)
			remaining = append(remaining, item)
		}
	}
	a.pending = remaining
}

func (a *Agent) enqueue(item pendingResult) {
	if len(a.pending) >= MaxPendingResults {
		// Самый старый результат к этому моменту уже не нужен никому.
		a.pending = a.pending[1:]
	}
	a.pending = append(a.pending, item)
}

// pauseFor вычисляет паузу после неудачи.
func (a *Agent) pauseFor(err error) time.Duration {
	var statusErr *transport.StatusError
	if errors.As(err, &statusErr) && statusErr.RetryAfter > 0 {
		// Своё представление о паузе агент уступает серверному.
		return statusErr.RetryAfter
	}
	return a.backoff.Next()
}

// RunOnce выполняет один проход цикла и возвращает паузу до следующего.
func (a *Agent) RunOnce(ctx context.Context) (time.Duration, error) {
	buffered, bufferBytes := 0, int64(0)
	if a.options.Buffer != nil {
		buffered, bufferBytes = a.options.Buffer.Stats()
	}
	response, err := a.options.Client.Heartbeat(ctx, transport.HeartbeatRequest{
		AgentVersion:   a.options.AgentVersion,
		ConfigVersion:  a.state.ConfigVersion,
		SentAt:         a.options.Now().UTC(),
		BufferedEvents: buffered,
		BufferBytes:    int(bufferBytes),
	})
	if err != nil {
		if transport.IsForbidden(err) {
			return 0, fmt.Errorf("%w: %v", ErrRevoked, err)
		}
		return a.pauseFor(err), err
	}

	a.backoff.Reset()
	a.lastHeartbeatAt = a.options.Now()

	if err := a.syncConfig(ctx, response.ConfigVersion); err != nil {
		// Неудача с конфигурацией не повод пропускать команды.
		slog.Warn("конфигурация не обновлена", "error", err)
	}

	a.flushPending(ctx)

	for _, command := range response.Commands {
		result := a.dispatcher.Execute(ctx, command)
		if err := a.options.Client.CommandResult(ctx, command.ID, result); err != nil {
			slog.Warn("результат отложен", "command_id", command.ID, "error", err)
			a.enqueue(pendingResult{commandID: command.ID, body: result})
		}
	}

	a.flushEvents(ctx)

	interval := time.Duration(response.HeartbeatIntervalSeconds) * time.Second
	if interval <= 0 {
		interval = fallbackInterval
	}
	return JitterInterval(interval, a.random), nil
}

// Run крутит цикл до отмены контекста.
func (a *Agent) Run(ctx context.Context) error {
	stopEvents := a.StartEvents(ctx)
	defer stopEvents()

	for {
		pause, err := a.RunOnce(ctx)
		if errors.Is(err, ErrRevoked) {
			return err
		}
		if err != nil {
			slog.Error("проход цикла не удался", "error", err, "retry_in", pause)
		}

		select {
		case <-ctx.Done():
			return nil
		case <-time.After(pause):
		}
	}
}

// MaybeRenew продлевает сертификат, если истекло 2/3 его срока.
//
// Новая ключевая пара обязательна: переиспользование старого ключа означало бы,
// что однажды случившаяся компрометация переживает все продления.
func (a *Agent) MaybeRenew(ctx context.Context, leaf *x509.Certificate) error {
	a.certNotAfter = leaf.NotAfter
	if !keystore.RenewalDue(leaf, a.options.Now()) {
		return nil
	}

	key, err := keystore.GenerateKey()
	if err != nil {
		return err
	}
	csrPEM, err := keystore.CreateCSR(key)
	if err != nil {
		return err
	}
	response, err := a.options.Client.Renew(ctx, csrPEM)
	if err != nil {
		return err
	}
	keyPEM, err := keystore.EncodeKey(key)
	if err != nil {
		return err
	}

	// Запись только после успешного ответа: неудача продления оставляет
	// действующий сертификат нетронутым, и у агента есть 30 суток запаса.
	if err := keystore.Save(a.options.Layout, a.options.Guard, keyPEM, []byte(response.CertificatePEM)); err != nil {
		return err
	}
	slog.Info("сертификат продлён", "not_after", response.NotAfter)
	return nil
}
