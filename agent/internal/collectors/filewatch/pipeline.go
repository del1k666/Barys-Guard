package filewatch

import (
	"log/slog"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/identity"
	"github.com/barysguard/agent/internal/volumes"
)

// Attributor определяет процесс, работавший с файлом. Реализация на Restart
// Manager — лучшая из доступных без драйвера; ETW подключается позже за тем же
// интерфейсом.
type Attributor interface {
	Attribute(path string) (map[string]any, bool)
}

const (
	indexCapacity = 10_000
	indexTTL      = 24 * time.Hour
)

type PipelineDeps struct {
	Config     Config
	Volumes    func() []volumes.Volume
	Hasher     Hasher
	Identity   identity.Resolver
	Attributor Attributor
	Emit       func(events.Envelope)
	Now        func() time.Time
}

// Pipeline превращает поток уведомлений в события. Не потокобезопасен:
// Handle и Tick вызываются из одного цикла.
type Pipeline struct {
	deps    PipelineDeps
	deb     *Debouncer
	idx     *HashIndex
	ex      *Excluder
	lim     *Limiter
	dropped uint64
}

func NewPipeline(deps PipelineDeps) *Pipeline {
	return &Pipeline{
		deps: deps,
		deb:  NewDebouncer(),
		idx:  NewHashIndex(indexCapacity, indexTTL),
		ex:   NewExcluder(deps.Config.Exclude),
		lim:  NewLimiter(deps.Config.MaxEventsPerSecond),
	}
}

func (p *Pipeline) Handle(raw Raw) {
	now := p.deps.Now()
	switch raw.Kind {
	case Created:
		if !p.ex.Match(raw.Path) {
			p.deb.Notify(raw.Path, OpCreate, now)
		}
	case Modified:
		if !p.ex.Match(raw.Path) {
			p.deb.Notify(raw.Path, OpModify, now)
		}
	case Deleted:
		if !p.ex.Match(raw.Path) {
			p.deb.Notify(raw.Path, OpDelete, now)
		}
	case Renamed:
		switch {
		case p.ex.Match(raw.Path):
			// Файл стал временным: прежнее имя исчезло.
			if !p.ex.Match(raw.OldPath) {
				p.deb.Notify(raw.OldPath, OpDelete, now)
			}
		case p.ex.Match(raw.OldPath):
			// Временный файл превратился в настоящий (так сохраняют редакторы):
			// для оператора это появление нового файла.
			p.deb.Notify(raw.Path, OpCreate, now)
		default:
			p.deb.NotifyRename(raw.OldPath, raw.Path, now)
		}
	}
}

// Tick выпускает событие по каждому установившемуся файлу и, если что-то
// отброшено ограничителем, сообщает об этом отдельным событием.
func (p *Pipeline) Tick() {
	now := p.deps.Now()
	cfg := p.deps.Config
	for _, settled := range p.deb.Settle(now, cfg.Stable, cfg.MaxWait) {
		p.emit(settled, now)
	}
	if p.dropped > 0 {
		report, err := events.NewEnvelope(events.ChannelAgent, "events_dropped", events.SeverityInfo, map[string]any{
			"component": "filewatch",
			"detail":    "слишком много файловых событий в секунду, часть отброшена",
			"count":     p.dropped,
		})
		if err == nil {
			p.deps.Emit(report)
		}
		p.dropped = 0
	}
}

func (p *Pipeline) emit(settled Settled, now time.Time) {
	// Ограничитель стоит до хеширования: цель — не нагружать диск, а не
	// только очередь.
	if !p.lim.Allow(now) {
		p.dropped++
		return
	}

	cfg := p.deps.Config
	vol := VolumeFor(settled.Path, p.deps.Volumes())
	removable := vol.Type == volumes.TypeRemovable

	in := EventInput{Action: settled.Action, DstPath: settled.Path, OldPath: settled.OldPath, Volume: vol}
	if settled.Action != ActionDelete {
		in.Hash = p.deps.Hasher.Hash(settled.Path, cfg.MaxHashBytes, now.Add(cfg.MaxWait))
		if in.Hash.Status == HashGone {
			return // временный файл или каталог
		}
	}

	if in.Hash.Status == HashOK && (settled.Action == ActionCreate || settled.Action == ActionModify) {
		if removable {
			if source, ok := p.idx.Lookup(in.Hash.SHA256, in.Hash.Size, now); ok && source != settled.Path {
				in.Action, in.SrcPath = ActionCopy, source
			}
		} else {
			p.idx.Put(in.Hash.SHA256, in.Hash.Size, settled.Path, now)
		}
	}

	if p.deps.Identity != nil {
		in.Actor = identity.Pick(p.deps.Identity.FileOwner(settled.Path), p.deps.Identity.ConsoleUser())
	}
	// Процесс определяется только для событий на внешних томах: Restart Manager
	// дорог, а интересны именно они.
	if removable && settled.Action != ActionDelete && p.deps.Attributor != nil {
		if process, ok := p.deps.Attributor.Attribute(settled.Path); ok {
			in.Process = process
		}
	}

	env, err := BuildEvent(in)
	if err != nil {
		slog.Error("событие файла не создано", "path", settled.Path, "error", err)
		return
	}
	p.deps.Emit(env)
}
