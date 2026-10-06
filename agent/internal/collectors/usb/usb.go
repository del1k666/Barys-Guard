// Package usb — сборщик канала usb: подключение и отключение внешних носителей.
package usb

import (
	"context"
	"log/slog"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

type Collector struct {
	hub         *volumes.Hub
	consoleUser func() map[string]any
}

// New. consoleUser может быть nil: тогда актёр не заполняется.
func New(hub *volumes.Hub, consoleUser func() map[string]any) *Collector {
	return &Collector{hub: hub, consoleUser: consoleUser}
}

func (c *Collector) Name() string { return "usb" }

// Run события порождает только для томов на шине USB: внутренние диски
// и сетевые диски в канал usb не попадают.
func (c *Collector) Run(ctx context.Context, emit func(events.Envelope)) error {
	changes, cancel := c.hub.Subscribe()
	defer cancel()

	for {
		select {
		case <-ctx.Done():
			return nil
		case change, ok := <-changes:
			if !ok {
				return nil
			}
			if change.Volume.Bus != volumes.BusUSB {
				continue
			}
			var actor map[string]any
			if c.consoleUser != nil {
				actor = c.consoleUser()
			}
			env, err := BuildEvent(change, actor)
			if err != nil {
				slog.Error("событие usb не создано", "volume", change.Volume.Key(), "error", err)
				continue
			}
			emit(env)
		}
	}
}

// BuildEvent описывает подключение или отключение носителя (раздел 7.2 спеки).
func BuildEvent(change volumes.Change, actor map[string]any) (events.Envelope, error) {
	action, severity := "unmount", events.SeverityInfo
	if change.Mounted {
		action, severity = "mount", events.SeverityLow
	}
	v := change.Volume
	env, err := events.NewEnvelope(events.ChannelUSB, action, severity, map[string]any{
		"drive_letter": v.DriveLetter,
		"size_bytes":   v.SizeBytes,
		"volume": map[string]any{
			"type": v.Type, "serial": v.Serial, "label": v.Label, "fs": v.FS,
		},
		"device": map[string]any{
			"bus": v.Bus, "vendor": v.Vendor, "product": v.Product, "serial": v.DeviceSerial,
		},
	})
	if err != nil {
		return events.Envelope{}, err
	}
	env.Actor = actor
	return env, nil
}
