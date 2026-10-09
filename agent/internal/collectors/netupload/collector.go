package netupload

import (
	"context"
	"log/slog"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/identity"
	"github.com/barysguard/agent/internal/volumes"
)

const (
	eventQueue     = 4096
	jobQueue       = 64
	hashDeadline   = 5 * time.Second
	defaultReport  = time.Minute
	dnsTTL         = 5 * time.Minute
	dnsCacheSize   = 8192
	maxProcesses   = 256
	maxReadsPerPID = 64
	// readRetention — сколько хранится запись о чтении файла.
	readRetention = 10 * time.Minute
)

type Deps struct {
	Config      Config
	Source      Source
	Stager      artifacts.Stager
	Hasher      filewatch.Hasher
	Stat        func(path string) (int64, error)
	Identity    identity.Resolver
	ProcessInfo func(pid uint32) map[string]any
	Volumes     func() []volumes.Volume
	Now         func() time.Time
	// ReportEvery — как часто сообщать о потерянных событиях.
	ReportEvery time.Duration
}

// Collector реализует events.Collector.
type Collector struct {
	deps    Deps
	filter  *Filter
	dns     *DNSCache
	reads   *Reads
	matcher *Matcher
	dropped atomic.Uint64
}

func New(deps Deps) *Collector {
	if deps.Hasher.Open == nil {
		deps.Hasher = filewatch.NewHasher()
	}
	if deps.Stat == nil {
		deps.Stat = statSize
	}
	if deps.Now == nil {
		deps.Now = time.Now
	}
	if deps.ReportEvery <= 0 {
		deps.ReportEvery = defaultReport
	}
	dns := NewDNSCache(dnsCacheSize)
	reads := NewReads(readRetention, maxProcesses, maxReadsPerPID)
	resolver := &Resolver{Catalog: NewCatalog(deps.Config.Services), DNS: dns}
	return &Collector{
		deps: deps, filter: NewFilter(deps.Config), dns: dns, reads: reads,
		matcher: NewMatcher(deps.Config, reads, resolver),
	}
}

func statSize(path string) (int64, error) {
	info, err := os.Stat(path)
	if err != nil {
		return 0, err
	}
	if info.IsDir() {
		return 0, os.ErrInvalid
	}
	return info.Size(), nil
}

func (c *Collector) Name() string { return "netupload" }

// enqueue кладёт событие источника в очередь; переполнение учитывается.
func (c *Collector) enqueue(in chan<- Event, ev Event) {
	select {
	case in <- ev:
	default:
		c.dropped.Add(1)
	}
}

func (c *Collector) Run(ctx context.Context, emit func(events.Envelope)) error {
	var workers sync.WaitGroup
	defer workers.Wait()
	ctx, cancel := context.WithCancel(ctx)
	defer cancel() // отменяется раньше workers.Wait: defer выполняются в обратном порядке

	in := make(chan Event, eventQueue)
	jobs := make(chan Match, jobQueue)
	sourceDone := make(chan error, 1)
	go func() {
		sourceDone <- c.deps.Source.Run(ctx, func(ev Event) { c.enqueue(in, ev) })
	}()

	// Снятие копии читает диск и может ждать занятый файл: отдельный поток,
	// чтобы разбор событий не стоял.
	workers.Add(1)
	go func() {
		defer workers.Done()
		for {
			select {
			case <-ctx.Done():
				return
			case match := <-jobs:
				c.report(match, emit)
			}
		}
	}()

	ticker := time.NewTicker(c.deps.ReportEvery)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case err := <-sourceDone:
			if err != nil && ctx.Err() == nil {
				slog.Warn("сборщик netupload недоступен", "error", err)
				c.emitAgent(emit, "netupload_unavailable", events.SeverityMedium, map[string]any{
					"component": "netupload", "detail": err.Error(),
				})
			}
			return nil
		case ev := <-in:
			c.handle(ev, jobs)
		case <-ticker.C:
			if count := c.dropped.Swap(0); count > 0 {
				c.emitAgent(emit, "netupload_dropped", events.SeverityLow, map[string]any{
					"component": "netupload", "count": count,
				})
			}
		}
	}
}

func (c *Collector) emitAgent(emit func(events.Envelope), action, severity string, subject map[string]any) {
	env, err := events.NewEnvelope(events.ChannelAgent, action, severity, subject)
	if err != nil {
		slog.Warn("не удалось создать служебное событие", "action", action, "error", err)
		return
	}
	emit(env)
}

func (c *Collector) handle(ev Event, jobs chan<- Match) {
	switch ev.Kind {
	case KindDNS:
		ttl := ev.TTL
		if ttl <= 0 {
			ttl = dnsTTL
		}
		c.dns.Learn(ev.Names, ev.Addrs, ttl, ev.At)
	case KindRead:
		if ev.Path == "" || !c.filter.PathOK(ev.Path) {
			return
		}
		size, err := c.deps.Stat(ev.Path)
		if err != nil || !c.filter.SizeOK(size) {
			return
		}
		c.reads.Add(ev.PID, Read{Path: ev.Path, Size: size, At: ev.At})
	case KindSend:
		for _, match := range c.matcher.Observe(Send{PID: ev.PID, Addr: ev.Addr, Bytes: ev.Bytes, At: ev.At}) {
			select {
			case jobs <- match:
			default:
				c.dropped.Add(1)
			}
		}
	}
}

// report снимает копию и отправляет событие. Исчезнувший файл события не даёт.
func (c *Collector) report(match Match, emit func(events.Envelope)) {
	deadline := c.deps.Now().Add(hashDeadline)
	hash := c.deps.Hasher.HashStaged(match.Read.Path, c.deps.Config.MaxFileBytes, deadline, c.deps.Stager)
	if hash.Status == filewatch.HashGone {
		return
	}
	var vols []volumes.Volume
	if c.deps.Volumes != nil {
		vols = c.deps.Volumes()
	}
	env, err := BuildEvent(match, hash, filewatch.VolumeFor(match.Read.Path, vols))
	if err != nil {
		slog.Warn("не удалось собрать событие отправки файла", "error", err)
		return
	}
	if c.deps.ProcessInfo != nil {
		env.Process = c.deps.ProcessInfo(match.PID)
	}
	if env.Process == nil {
		env.Labels["process"] = "unknown"
	}
	env.Actor = c.actor(match.Read.Path)
	emit(env)
}

func (c *Collector) actor(path string) map[string]any {
	if c.deps.Identity == nil {
		return nil
	}
	return identity.Pick(c.deps.Identity.FileOwner(path), c.deps.Identity.ConsoleUser())
}
