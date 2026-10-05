package filewatch

import (
	"context"
	"io"
	"io/fs"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

type fixedProvider struct {
	mu   sync.Mutex
	vols []volumes.Volume
}

func (f *fixedProvider) Snapshot() ([]volumes.Volume, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]volumes.Volume(nil), f.vols...), nil
}

func (f *fixedProvider) set(v []volumes.Volume) { f.mu.Lock(); f.vols = v; f.mu.Unlock() }

// watchLog запоминает, какие корни просили наблюдать, и даёт сценарию
// подсунуть уведомления.
type watchLog struct {
	mu      sync.Mutex
	started map[string]chan<- Raw
	stopped map[string]bool
	denied  map[string]error
}

func newWatchLog() *watchLog {
	return &watchLog{started: map[string]chan<- Raw{}, stopped: map[string]bool{}, denied: map[string]error{}}
}

func (w *watchLog) start(ctx context.Context, root string, out chan<- Raw, _ func(string)) error {
	w.mu.Lock()
	if err, ok := w.denied[root]; ok {
		w.mu.Unlock()
		return err
	}
	w.started[root] = out
	w.mu.Unlock()
	<-ctx.Done()
	w.mu.Lock()
	w.stopped[root] = true
	w.mu.Unlock()
	return nil
}

func (w *watchLog) out(root string) (chan<- Raw, bool) {
	w.mu.Lock()
	defer w.mu.Unlock()
	c, ok := w.started[root]
	return c, ok
}

func (w *watchLog) wasStopped(root string) bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.stopped[root]
}

func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("не дождались: %s", what)
}

type collected struct {
	mu   sync.Mutex
	list []events.Envelope
}

func (c *collected) add(e events.Envelope) { c.mu.Lock(); c.list = append(c.list, e); c.mu.Unlock() }
func (c *collected) snapshot() []events.Envelope {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]events.Envelope(nil), c.list...)
}

func startCollector(t *testing.T, cfg Config, provider *fixedProvider, log *watchLog, files map[string]string) (*collected, func()) {
	t.Helper()
	hub := volumes.NewHub(provider, 20*time.Millisecond)
	hasher := Hasher{
		Open: func(path string) (io.ReadCloser, int64, error) {
			content, ok := files[path]
			if !ok {
				return nil, 0, fs.ErrNotExist
			}
			return io.NopCloser(strings.NewReader(content)), int64(len(content)), nil
		},
		Sleep: func(time.Duration) {}, Now: time.Now,
	}
	collector := New(Deps{Config: cfg, Hub: hub, Hasher: hasher, StartWatcher: log.start, Now: time.Now, RootExists: func(string) bool { return true }})

	got := &collected{}
	ctx, cancel := context.WithCancel(context.Background())
	hubDone := make(chan struct{})
	done := make(chan struct{})
	go func() { hub.Run(ctx, nil); close(hubDone) }()
	go func() { collector.Run(ctx, got.add); close(done) }()
	return got, func() { cancel(); <-done; <-hubDone }
}

var quick = Config{Enabled: true, Stable: 30 * time.Millisecond, MaxWait: time.Second, MaxHashBytes: 1 << 20, MaxEventsPerSecond: 1000}

func TestCollectorWatchesConfiguredPathsAndEmitsFileEvents(t *testing.T) {
	log := newWatchLog()
	provider := &fixedProvider{vols: []volumes.Volume{{DriveLetter: "C:", Type: volumes.TypeFixed, Serial: "AAAA"}}}
	cfg := quick
	cfg.Paths = []string{`C:\Users\u\Documents`}
	got, stop := startCollector(t, cfg, provider, log, map[string]string{`C:\Users\u\Documents\a.txt`: "данные"})
	defer stop()

	eventually(t, "наблюдатель папки запущен", func() bool { _, ok := log.out(`C:\Users\u\Documents`); return ok })
	out, _ := log.out(`C:\Users\u\Documents`)
	out <- Raw{Kind: Created, Path: `C:\Users\u\Documents\a.txt`}

	eventually(t, "событие file/create", func() bool {
		for _, e := range got.snapshot() {
			if e.Channel == "file" && e.Action == "create" {
				return true
			}
		}
		return false
	})
}

func TestRemovableVolumeRootIsWatchedWhileItIsMounted(t *testing.T) {
	log := newWatchLog()
	provider := &fixedProvider{}
	cfg := quick
	cfg.Paths = nil
	_, stop := startCollector(t, cfg, provider, log, nil)
	defer stop()

	provider.set([]volumes.Volume{{DriveLetter: "E:", Type: volumes.TypeRemovable, Bus: volumes.BusUSB, Serial: "0781"}})
	eventually(t, "корень флешки наблюдается", func() bool { _, ok := log.out(`E:\`); return ok })

	// Извлечение флешки останавливает наблюдателя: дескриптор не должен утечь.
	provider.set(nil)
	eventually(t, "наблюдатель флешки остановлен", func() bool { return log.wasStopped(`E:\`) })
}

func TestFixedVolumesAreNotWatchedAsRemovable(t *testing.T) {
	log := newWatchLog()
	provider := &fixedProvider{vols: []volumes.Volume{{DriveLetter: "D:", Type: volumes.TypeFixed, Serial: "DDDD"}}}
	cfg := quick
	cfg.Paths = nil
	_, stop := startCollector(t, cfg, provider, log, nil)
	defer stop()

	time.Sleep(150 * time.Millisecond)

	if _, ok := log.out(`D:\`); ok {
		t.Fatal("фиксированный том не должен наблюдаться целиком")
	}
}

// Нет доступа к папке другого профиля: сообщаем и продолжаем с остальными.
func TestDeniedRootIsReportedAndOthersKeepWorking(t *testing.T) {
	log := newWatchLog()
	log.denied[`C:\Users\other\Documents`] = fs.ErrPermission
	provider := &fixedProvider{vols: []volumes.Volume{{DriveLetter: "C:", Type: volumes.TypeFixed, Serial: "AAAA"}}}
	cfg := quick
	cfg.Paths = []string{`C:\Users\other\Documents`, `C:\Users\me\Documents`}
	got, stop := startCollector(t, cfg, provider, log, nil)
	defer stop()

	eventually(t, "своя папка наблюдается", func() bool { _, ok := log.out(`C:\Users\me\Documents`); return ok })
	eventually(t, "agent/watch_denied по чужой папке", func() bool {
		for _, e := range got.snapshot() {
			if e.Channel == "agent" && e.Action == "watch_denied" && strings.Contains(e.Subject["detail"].(string), `other`) {
				return true
			}
		}
		return false
	})
}

func TestMissingRootsAreSilentlySkipped(t *testing.T) {
	log := newWatchLog()
	log.denied[`C:\Users\u\Desktop`] = fs.ErrNotExist
	provider := &fixedProvider{}
	cfg := quick
	cfg.Paths = []string{`C:\Users\u\Desktop`}
	got, stop := startCollector(t, cfg, provider, log, nil)
	defer stop()

	time.Sleep(150 * time.Millisecond)

	for _, e := range got.snapshot() {
		if e.Action == "watch_denied" {
			t.Fatalf("отсутствующая папка не повод для события: %+v", e)
		}
	}
}

func TestCollectorStopsOnCancelAndStopsItsWatchers(t *testing.T) {
	log := newWatchLog()
	provider := &fixedProvider{}
	cfg := quick
	cfg.Paths = []string{`C:\Users\u\Documents`}
	_, stop := startCollector(t, cfg, provider, log, nil)

	eventually(t, "наблюдатель запущен", func() bool { _, ok := log.out(`C:\Users\u\Documents`); return ok })
	stop()

	if !log.wasStopped(`C:\Users\u\Documents`) {
		t.Fatal("наблюдатель не остановлен вместе со сборщиком")
	}
}
