package usb_test

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/collectors/usb"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

type provider struct{ vols []volumes.Volume }

func (p *provider) Snapshot() ([]volumes.Volume, error) { return p.vols, nil }

var flash = volumes.Volume{
	DriveLetter: "E:", Serial: "0781-5583", Label: "KINGSTON", FS: "FAT32", SizeBytes: 32015679488,
	Type: volumes.TypeRemovable, Bus: volumes.BusUSB, Vendor: "Kingston", Product: "DataTraveler 3.0", DeviceSerial: "0019E06B",
}

func TestBuildEventDescribesTheDevice(t *testing.T) {
	env, err := usb.BuildEvent(volumes.Change{Mounted: true, Volume: flash}, map[string]any{"user_name": "PC\\ivanov"})
	if err != nil {
		t.Fatal(err)
	}

	if env.Channel != "usb" || env.Action != "mount" || env.SeverityHint != events.SeverityLow {
		t.Fatalf("конверт: %+v", env)
	}
	if env.Subject["drive_letter"] != "E:" || env.Subject["size_bytes"] != int64(32015679488) {
		t.Fatalf("subject: %+v", env.Subject)
	}
	volume := env.Subject["volume"].(map[string]any)
	device := env.Subject["device"].(map[string]any)
	if volume["type"] != "removable" || volume["serial"] != "0781-5583" || volume["fs"] != "FAT32" {
		t.Errorf("volume: %+v", volume)
	}
	if device["bus"] != "usb" || device["vendor"] != "Kingston" || device["product"] != "DataTraveler 3.0" || device["serial"] != "0019E06B" {
		t.Errorf("device: %+v", device)
	}
	if env.Actor["user_name"] != "PC\\ivanov" {
		t.Errorf("actor: %+v", env.Actor)
	}

	off, _ := usb.BuildEvent(volumes.Change{Mounted: false, Volume: flash}, nil)
	if off.Action != "unmount" || off.SeverityHint != events.SeverityInfo {
		t.Errorf("unmount: %+v", off)
	}
}

func TestOnlyUSBVolumesProduceEvents(t *testing.T) {
	provider := &provider{vols: []volumes.Volume{
		flash,
		{DriveLetter: "C:", Serial: "AAAA", Type: volumes.TypeFixed, Bus: volumes.BusOther},
	}}
	hub := volumes.NewHub(provider, 0)
	collector := usb.New(hub, func() map[string]any { return map[string]any{"user_name": "u"} })

	var mu sync.Mutex
	var got []events.Envelope
	emitted := make(chan struct{}, 8)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		collector.Run(ctx, func(e events.Envelope) {
			mu.Lock()
			got = append(got, e)
			mu.Unlock()
			emitted <- struct{}{}
		})
		close(done)
	}()

	time.Sleep(20 * time.Millisecond) // подписка до опроса
	hub.Poll()
	select {
	case <-emitted:
	case <-time.After(2 * time.Second):
		t.Fatal("событие подключения не пришло")
	}
	provider.vols = nil
	hub.Poll()
	select {
	case <-emitted:
	case <-time.After(2 * time.Second):
		t.Fatal("событие отключения не пришло")
	}
	cancel()
	<-done

	mu.Lock()
	defer mu.Unlock()
	if len(got) != 2 || got[0].Action != "mount" || got[1].Action != "unmount" {
		t.Fatalf("события: %+v", got)
	}
	if got[0].Actor["user_name"] != "u" {
		t.Errorf("актёр не подставлен: %+v", got[0].Actor)
	}
}

func TestStopsOnContextCancel(t *testing.T) {
	collector := usb.New(volumes.NewHub(&provider{}, 0), nil)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- collector.Run(ctx, func(events.Envelope) {}) }()

	cancel()

	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("сборщик не остановился")
	}
	if collector.Name() != "usb" {
		t.Fatalf("Name = %q", collector.Name())
	}
}
