// Package lifecycle — сборщик канала agent: старт и остановка агента.
package lifecycle

import (
	"context"
	"log/slog"

	"github.com/barysguard/agent/internal/events"
)

type Collector struct{ version string }

func New(version string) *Collector { return &Collector{version: version} }

func (c *Collector) Name() string { return "lifecycle" }

// Run порождает start сразу и stop при отмене контекста. Событие stop
// уходит в очередь, которая закрывается только после возврата всех сборщиков,
// поэтому оно доходит до буфера.
func (c *Collector) Run(ctx context.Context, emit func(events.Envelope)) error {
	c.emit(emit, "start", "version "+c.version)
	<-ctx.Done()
	c.emit(emit, "stop", "остановка по сигналу")
	return nil
}

func (c *Collector) emit(emit func(events.Envelope), action, detail string) {
	env, err := events.NewEnvelope(events.ChannelAgent, action, events.SeverityInfo, map[string]any{
		"component": "agent",
		"detail":    detail,
	})
	if err != nil {
		slog.Error("событие жизненного цикла не создано", "action", action, "error", err)
		return
	}
	emit(env)
}
