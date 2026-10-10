package collectors

import (
	"context"
	"testing"

	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/collectors/netupload"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/identity"
	"github.com/barysguard/agent/internal/volumes"
)

type noVolumes struct{}

func (noVolumes) Snapshot() ([]volumes.Volume, error) { return nil, nil }

func fakePlatform() Platform {
	return Platform{
		Supported:    true,
		Provider:     noVolumes{},
		Identity:     identity.New(),
		StartWatcher: func(context.Context, string, chan<- filewatch.Raw, func(string)) error { return nil },
		Profiles:     func() []string { return []string{`C:\Users\u`} },
	}
}

func names(list []events.Collector) []string {
	var out []string
	for _, c := range list {
		out = append(out, c.Name())
	}
	return out
}

func equal(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func TestDefaultDocumentStartsHubUSBAndFilewatch(t *testing.T) {
	got := names(Build(nil, `C:\data`, fakePlatform()))

	if !equal(got, []string{"volumes", "usb", "filewatch"}) {
		t.Fatalf("сборщики: %v", got)
	}
}

func TestDisabledChannelsAreLeftOut(t *testing.T) {
	onlyFiles := map[string]any{"collectors": map[string]any{"usb": map[string]any{"enabled": false}}}
	if got := names(Build(onlyFiles, "", fakePlatform())); !equal(got, []string{"volumes", "filewatch"}) {
		t.Fatalf("без usb: %v", got)
	}

	onlyUSB := map[string]any{"collectors": map[string]any{"file_watch": map[string]any{"enabled": false}}}
	if got := names(Build(onlyUSB, "", fakePlatform())); !equal(got, []string{"volumes", "usb"}) {
		t.Fatalf("без файлов: %v", got)
	}

	none := map[string]any{"collectors": map[string]any{
		"usb": map[string]any{"enabled": false}, "file_watch": map[string]any{"enabled": false},
	}}
	// Опрос томов нужен только сборщикам; без них он не запускается.
	if got := Build(none, "", fakePlatform()); len(got) != 0 {
		t.Fatalf("всё выключено, а сборщики есть: %v", names(got))
	}
}

func TestUnsupportedPlatformBuildsNothing(t *testing.T) {
	plat := fakePlatform()
	plat.Supported = false

	if got := NewFactoryFor(plat, "")(nil); len(got) != 0 {
		t.Fatalf("на неподдерживаемой платформе сборщики: %v", names(got))
	}
}

type fakeNetSource struct{}

func (fakeNetSource) Run(context.Context, func(netupload.Event)) error { return nil }

func hasCollector(list []events.Collector, name string) bool {
	for _, c := range list {
		if c.Name() == name {
			return true
		}
	}
	return false
}

func TestNetUploadIsAddedWhenASourceExists(t *testing.T) {
	plat := fakePlatform()
	plat.NetSource = fakeNetSource{}

	list := Build(map[string]any{}, t.TempDir(), plat)

	if !hasCollector(list, "netupload") {
		t.Fatalf("netupload должен быть в группе: %v", names(list))
	}

	off := map[string]any{"collectors": map[string]any{"net_upload": map[string]any{"enabled": false}}}
	if hasCollector(Build(off, t.TempDir(), plat), "netupload") {
		t.Fatal("выключенный net_upload не должен запускаться")
	}
}

func TestNetUploadAbsentWithoutASource(t *testing.T) {
	plat := fakePlatform() // NetSource == nil

	if hasCollector(Build(map[string]any{}, t.TempDir(), plat), "netupload") {
		t.Fatal("без источника сборщика нет (Linux, Docker-стенд)")
	}
}

func TestNetUploadAloneStartsTheGroup(t *testing.T) {
	plat := fakePlatform()
	plat.NetSource = fakeNetSource{}
	doc := map[string]any{"collectors": map[string]any{
		"usb":        map[string]any{"enabled": false},
		"file_watch": map[string]any{"enabled": false},
	}}

	list := Build(doc, t.TempDir(), plat)

	if !hasCollector(list, "netupload") {
		t.Fatalf("netupload должен работать и без usb/file_watch: %v", names(list))
	}
}
