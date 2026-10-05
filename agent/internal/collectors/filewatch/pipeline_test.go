package filewatch

import (
	"fmt"
	"io"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

var errNotExist = os.ErrNotExist

type fakeIdentity struct {
	owner, console map[string]any
}

func (f fakeIdentity) FileOwner(string) map[string]any { return f.owner }
func (f fakeIdentity) ConsoleUser() map[string]any     { return f.console }

type fakeAttributor struct{ process map[string]any }

func (f fakeAttributor) Attribute(string) (map[string]any, bool) { return f.process, f.process != nil }

type harness struct {
	pipeline *Pipeline
	emitted  []events.Envelope
	now      time.Time
	files    map[string]string
}

// newHarness: часы заморожены (Now возвращает h.now), поэтому отсутствующий файл
// обязан сообщаться как os.ErrNotExist: любая другая ошибка запускала бы цикл
// повторов хешера, который при замороженных часах не кончился бы.
func newHarness(t *testing.T, mutate func(*PipelineDeps, *Config)) *harness {
	t.Helper()
	h := &harness{now: t0, files: map[string]string{}}
	cfg := Config{Enabled: true, Stable: 1500 * time.Millisecond, MaxWait: 30 * time.Second, MaxHashBytes: 1 << 20, MaxEventsPerSecond: 1000}
	deps := PipelineDeps{
		Volumes: func() []volumes.Volume {
			return []volumes.Volume{
				{DriveLetter: "C:", Type: volumes.TypeFixed, Serial: "AAAA-BBBB"},
				{DriveLetter: "E:", Type: volumes.TypeRemovable, Serial: "0781-5583", Label: "K"},
			}
		},
		Hasher: Hasher{
			Open: func(path string) (io.ReadCloser, int64, error) {
				content, ok := h.files[path]
				if !ok {
					return nil, 0, os.ErrNotExist
				}
				return io.NopCloser(strings.NewReader(content)), int64(len(content)), nil
			},
			Sleep: func(time.Duration) {}, Now: func() time.Time { return h.now },
		},
		Identity: fakeIdentity{
			owner:   map[string]any{"user_sid": "S-1-5-21-1-2-3-1001", "user_name": "PC\\ivanov"},
			console: map[string]any{"user_sid": "S-1-5-21-1-2-3-1002", "user_name": "PC\\console"},
		},
		Emit: func(e events.Envelope) { h.emitted = append(h.emitted, e) },
		Now:  func() time.Time { return h.now },
	}
	if mutate != nil {
		mutate(&deps, &cfg)
	}
	deps.Config = cfg
	h.pipeline = NewPipeline(deps)
	return h
}

func (h *harness) advance(d time.Duration) { h.now = h.now.Add(d); h.pipeline.Tick() }

const src = `C:\Users\ivanov\Documents\отчёт.xlsx`
const dst = `E:\отчёт.xlsx`

func TestFileCopiedToAFlashDriveIsReportedAsCopyWithItsSource(t *testing.T) {
	h := newHarness(t, nil)
	h.files[src] = "содержимое отчёта"
	h.files[dst] = "содержимое отчёта"

	// Агент видит файл в наблюдаемой папке…
	h.pipeline.Handle(Raw{Kind: Modified, Path: src})
	h.advance(2 * time.Second)
	// …потом он появляется на флешке.
	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if len(h.emitted) != 2 {
		t.Fatalf("событий %d: %+v", len(h.emitted), h.emitted)
	}
	if h.emitted[0].Action != "modify" || h.emitted[0].SeverityHint != events.SeverityInfo {
		t.Fatalf("первое: %+v", h.emitted[0])
	}
	copyEvent := h.emitted[1]
	if copyEvent.Action != "copy" || copyEvent.SeverityHint != events.SeverityHigh || copyEvent.Subject["src_path"] != src {
		t.Fatalf("копирование: %+v", copyEvent)
	}
	if copyEvent.Artifact == nil || copyEvent.Actor["user_name"] != "PC\\ivanov" {
		t.Fatalf("артефакт и актёр: %+v %+v", copyEvent.Artifact, copyEvent.Actor)
	}
}

func TestUnseenFileOnAFlashDriveIsAnOrdinaryCreate(t *testing.T) {
	h := newHarness(t, nil)
	h.files[dst] = "что-то, чего агент не видел"

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if len(h.emitted) != 1 || h.emitted[0].Action != "create" || h.emitted[0].SeverityHint != events.SeverityMedium {
		t.Fatalf("события: %+v", h.emitted)
	}
	if _, has := h.emitted[0].Subject["src_path"]; has {
		t.Fatalf("src_path без источника: %+v", h.emitted[0].Subject)
	}
}

func TestExcludedPathsAreIgnored(t *testing.T) {
	h := newHarness(t, func(_ *PipelineDeps, cfg *Config) { cfg.Exclude = []string{`*\~$*`, `*.tmp`} })
	h.files[`C:\d\~$a.docx`] = "x"

	h.pipeline.Handle(Raw{Kind: Created, Path: `C:\d\~$a.docx`})
	h.advance(5 * time.Second)

	if len(h.emitted) != 0 {
		t.Fatalf("исключённый файл дал событие: %+v", h.emitted)
	}
}

func TestSaveThroughATempFileIsOneCreateEvent(t *testing.T) {
	h := newHarness(t, func(_ *PipelineDeps, cfg *Config) { cfg.Exclude = []string{`*.tmp`} })
	h.files[`C:\d\report.docx`] = "новая версия"

	h.pipeline.Handle(Raw{Kind: Renamed, OldPath: `C:\d\~WRD0001.tmp`, Path: `C:\d\report.docx`})
	h.advance(2 * time.Second)

	if len(h.emitted) != 1 || h.emitted[0].Action != "create" {
		t.Fatalf("события: %+v", h.emitted)
	}
}

func TestVanishedFileProducesNoEvent(t *testing.T) {
	h := newHarness(t, nil) // файла в h.files нет

	h.pipeline.Handle(Raw{Kind: Created, Path: `C:\d\temp.dat`})
	h.advance(2 * time.Second)

	if len(h.emitted) != 0 {
		t.Fatalf("исчезнувший файл дал событие: %+v", h.emitted)
	}
	if errNotExist == nil {
		t.Fatal("sanity")
	}
}

func TestDeleteNeedsNoHash(t *testing.T) {
	h := newHarness(t, nil)

	h.pipeline.Handle(Raw{Kind: Deleted, Path: dst})
	h.advance(2 * time.Second)

	if len(h.emitted) != 1 || h.emitted[0].Action != "delete" || h.emitted[0].Artifact != nil {
		t.Fatalf("события: %+v", h.emitted)
	}
}

func TestProcessIsAttributedOnlyOnRemovableVolumes(t *testing.T) {
	h := newHarness(t, func(d *PipelineDeps, _ *Config) {
		d.Attributor = fakeAttributor{process: map[string]any{"pid": 77, "path": `C:\Windows\explorer.exe`}}
	})
	h.files[dst] = "a"
	h.files[src] = "b"

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.pipeline.Handle(Raw{Kind: Created, Path: src})
	h.advance(2 * time.Second)

	byPath := map[string]events.Envelope{}
	for _, e := range h.emitted {
		byPath[e.Subject["dst_path"].(string)] = e
	}
	if byPath[dst].Process["pid"] != 77 {
		t.Fatalf("на флешке процесс не определён: %+v", byPath[dst])
	}
	if byPath[src].Process != nil {
		t.Fatalf("в наблюдаемой папке процесс не запрашивается: %+v", byPath[src])
	}
}

func TestServiceOwnerFallsBackToTheConsoleUser(t *testing.T) {
	h := newHarness(t, func(d *PipelineDeps, _ *Config) {
		d.Identity = fakeIdentity{
			owner:   map[string]any{"user_sid": "S-1-5-32-544", "user_name": `BUILTIN\Administrators`},
			console: map[string]any{"user_sid": "S-1-5-21-1-2-3-1002", "user_name": "PC\\console"},
		}
	})
	h.files[dst] = "x"

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if h.emitted[0].Actor["user_name"] != "PC\\console" {
		t.Fatalf("актёр: %+v", h.emitted[0].Actor)
	}
}

// Распаковка архива: тысячи файлов за секунду не должны ни остановить сборщик,
// ни пройти все разом; потеря обязана быть сообщена.
func TestEventStormIsThrottledAndTheLossIsReported(t *testing.T) {
	h := newHarness(t, func(_ *PipelineDeps, cfg *Config) { cfg.MaxEventsPerSecond = 10 })
	for i := 0; i < 500; i++ {
		path := fmt.Sprintf(`C:\Users\ivanov\Downloads\arch\f%d.txt`, i)
		h.files[path] = "данные"
		h.pipeline.Handle(Raw{Kind: Created, Path: path})
	}
	h.advance(2 * time.Second)

	files, dropped := 0, 0
	for _, e := range h.emitted {
		switch {
		case e.Channel == "file":
			files++
		case e.Channel == "agent" && e.Action == "events_dropped":
			dropped += int(e.Subject["count"].(uint64))
		}
	}
	if files > 12 || files < 1 {
		t.Fatalf("прошло %d событий при лимите 10 в секунду", files)
	}
	if files+dropped < 400 {
		t.Fatalf("потери не сосчитаны: прошло %d, потеряно %d из ~500", files, dropped)
	}
}
