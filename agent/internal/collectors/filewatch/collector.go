package filewatch

import (
	"context"
	"errors"
	"io/fs"
	"log/slog"
	"os"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/identity"
	"github.com/barysguard/agent/internal/volumes"
)

const (
	tickInterval = 250 * time.Millisecond
	rawBuffer    = 4096
)

type Deps struct {
	Config       Config
	Hub          *volumes.Hub
	Identity     identity.Resolver
	Attributor   Attributor
	StartWatcher StartWatcher
	// Hasher и RootExists подменяются в тестах; нулевые значения дают боевые.
	Hasher     Hasher
	RootExists func(root string) bool
	Now        func() time.Time
}

type Collector struct{ deps Deps }

func New(deps Deps) *Collector {
	if deps.Now == nil {
		deps.Now = time.Now
	}
	if deps.Hasher.Open == nil {
		deps.Hasher = NewHasher()
	}
	if deps.StartWatcher == nil {
		deps.StartWatcher = DefaultStartWatcher
	}
	if deps.RootExists == nil {
		deps.RootExists = func(root string) bool { _, err := os.Stat(root); return err == nil }
	}
	return &Collector{deps: deps}
}

func (c *Collector) Name() string { return "filewatch" }

type root struct {
	cancel context.CancelFunc
	done   chan struct{}
}

// Run наблюдает за папками из конфигурации и за корнями внешних томов,
// пока те подключены. Всё — в одном цикле: уведомления, подключения томов
// и тик склейки обрабатываются последовательно, без общих блокировок.
func (c *Collector) Run(ctx context.Context, emit func(events.Envelope)) error {
	cfg := c.deps.Config
	raw := make(chan Raw, rawBuffer)
	pipeline := NewPipeline(PipelineDeps{
		Config:     cfg,
		Volumes:    func() []volumes.Volume { return c.deps.Hub.Current() },
		Hasher:     c.deps.Hasher,
		Identity:   c.deps.Identity,
		Attributor: c.deps.Attributor,
		Emit:       emit,
		Now:        c.deps.Now,
	})

	roots := map[string]*root{}
	report := func(action, path, detail string) {
		env, err := events.NewEnvelope(events.ChannelAgent, action, events.SeverityLow, map[string]any{
			"component": "filewatch",
			"detail":    detail + ": " + path,
		})
		if err == nil {
			emit(env)
		}
	}
	start := func(path string) {
		if _, running := roots[path]; running {
			return
		}
		if !c.deps.RootExists(path) {
			return // у профиля может не быть Desktop: это не событие
		}
		rootCtx, cancel := context.WithCancel(ctx)
		entry := &root{cancel: cancel, done: make(chan struct{})}
		roots[path] = entry
		go func() {
			defer close(entry.done)
			err := c.deps.StartWatcher(rootCtx, path, raw, func(overflowed string) {
				report("watch_overflow", overflowed, "буфер уведомлений переполнен, изменения потеряны")
			})
			switch {
			case err == nil, rootCtx.Err() != nil:
			case errors.Is(err, fs.ErrNotExist):
				slog.Debug("наблюдаемая папка исчезла", "path", path)
			case errors.Is(err, fs.ErrPermission):
				report("watch_denied", path, "нет доступа к наблюдаемой папке")
			default:
				slog.Warn("наблюдение за папкой остановилось", "path", path, "error", err)
				report("watch_denied", path, "наблюдение остановилось: "+err.Error())
			}
		}()
	}
	stop := func(path string) {
		if entry, ok := roots[path]; ok {
			entry.cancel()
			<-entry.done
			delete(roots, path)
		}
	}
	stopAll := func() {
		for path := range roots {
			stop(path)
		}
	}

	for _, path := range cfg.Paths {
		start(path)
	}

	changes, unsubscribe := c.deps.Hub.Subscribe()
	defer unsubscribe()
	ticker := time.NewTicker(tickInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			stopAll()
			return nil
		case item := <-raw:
			pipeline.Handle(item)
		case change, ok := <-changes:
			if !ok {
				stopAll()
				return nil
			}
			if change.Volume.Type != volumes.TypeRemovable {
				continue
			}
			rootPath := change.Volume.DriveLetter + `\`
			if change.Mounted {
				start(rootPath)
			} else {
				stop(rootPath)
			}
		case <-ticker.C:
			pipeline.Tick()
		}
	}
}
