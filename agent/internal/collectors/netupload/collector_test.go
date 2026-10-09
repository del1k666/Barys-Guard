package netupload

import (
	"bytes"
	"context"
	"errors"
	"io"
	"io/fs"
	"net/netip"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

var (
	errNotExist   = os.ErrNotExist
	errPermission = fs.ErrPermission
)

type scriptSource struct {
	feed []Event
	err  error
	done chan struct{}
}

func (s *scriptSource) Run(ctx context.Context, sink func(Event)) error {
	if s.err != nil {
		return s.err
	}
	for _, ev := range s.feed {
		sink(ev)
	}
	select {
	case <-ctx.Done():
	case <-s.done:
	}
	return nil
}

type collected struct {
	mu   sync.Mutex
	list []events.Envelope
}

func (c *collected) emit(env events.Envelope) {
	c.mu.Lock()
	c.list = append(c.list, env)
	c.mu.Unlock()
}

func (c *collected) snapshot() []events.Envelope {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]events.Envelope(nil), c.list...)
}

func (c *collected) waitFor(t *testing.T, count int) []events.Envelope {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if got := c.snapshot(); len(got) >= count {
			return got
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("ждали %d событий, получили %v", count, c.snapshot())
	return nil
}

type nopCloser struct{ io.Reader }

func (nopCloser) Close() error { return nil }

func hasherWith(content []byte, openErr error) filewatch.Hasher {
	return filewatch.Hasher{
		Open: func(string) (io.ReadCloser, int64, error) {
			if openErr != nil {
				return nil, 0, openErr
			}
			return nopCloser{bytes.NewReader(content)}, int64(len(content)), nil
		},
		Sleep: func(time.Duration) {},
		Now:   time.Now,
	}
}

func testDeps(source Source, hasher filewatch.Hasher) Deps {
	cfg := DefaultConfig()
	cfg.MinFileBytes = 10
	return Deps{
		Config: cfg, Source: source, Hasher: hasher,
		Stat: func(string) (int64, error) { return 1000, nil },
		ProcessInfo: func(pid uint32) map[string]any {
			return map[string]any{"pid": int(pid), "path": `C:\Chrome\chrome.exe`}
		},
		Volumes:     func() []volumes.Volume { return []volumes.Volume{{DriveLetter: "C:", Type: volumes.TypeFixed}} },
		Now:         time.Now,
		ReportEvery: time.Hour,
	}
}

func run(t *testing.T, deps Deps) (*collected, func()) {
	t.Helper()
	return runCollector(t, New(deps))
}

func runCollector(t *testing.T, c *Collector) (*collected, func()) {
	t.Helper()
	out := &collected{}
	ctx, cancel := context.WithCancel(context.Background())
	finished := make(chan struct{})
	go func() {
		c.Run(ctx, out.emit)
		close(finished)
	}()
	return out, func() {
		cancel()
		select {
		case <-finished:
		case <-time.After(3 * time.Second):
			t.Fatal("Run не вернулся после отмены контекста")
		}
	}
}

func uploadFeed(path string, size uint64) []Event {
	at := time.Now()
	return []Event{
		{Kind: KindDNS, Names: []string{"drive.google.com"}, Addrs: []netip.Addr{addr("142.250.1.1")}, TTL: time.Hour, At: at},
		{Kind: KindRead, PID: 9, Path: path, At: at},
		{Kind: KindSend, PID: 9, Addr: addr("142.250.1.1"), Bytes: size, At: at},
	}
}

func TestUploadProducesOneEventWithArtifact(t *testing.T) {
	source := &scriptSource{feed: uploadFeed(`C:\Users\a\Documents\plan.pdf`, 2000), done: make(chan struct{})}
	out, stop := run(t, testDeps(source, hasherWith([]byte("содержимое документа"), nil)))
	defer stop()

	got := out.waitFor(t, 1)
	env := got[0]

	if env.Channel != events.ChannelNetwork || env.Action != "upload" {
		t.Fatalf("%s/%s", env.Channel, env.Action)
	}
	if env.Subject["service"] != "gdrive" || env.Subject["src_path"] != `C:\Users\a\Documents\plan.pdf` {
		t.Fatalf("subject = %v", env.Subject)
	}
	if env.Artifact == nil || env.Artifact.SHA256 == "" {
		t.Fatalf("ждали артефакт: %+v", env.Artifact)
	}
	if env.Process["pid"] != 9 {
		t.Fatalf("process = %v", env.Process)
	}
	if len(out.snapshot()) != 1 {
		t.Fatalf("событие должно быть одно: %v", out.snapshot())
	}
}

func TestReadsOfOtherFilesAndSmallTrafficProduceNothing(t *testing.T) {
	at := time.Now()
	source := &scriptSource{feed: []Event{
		{Kind: KindDNS, Names: []string{"drive.google.com"}, Addrs: []netip.Addr{addr("142.250.1.1")}, TTL: time.Hour, At: at},
		{Kind: KindRead, PID: 9, Path: `C:\Users\a\Pictures\photo.jpg`, At: at},        // не тот тип
		{Kind: KindRead, PID: 9, Path: `C:\Users\a\AppData\Local\x\cache.pdf`, At: at}, // исключённый путь
		{Kind: KindRead, PID: 9, Path: ``, At: at},                                     // пустой путь
		{Kind: KindRead, PID: 9, Path: `C:\Users\a\Documents\plan.pdf`, At: at},        // подходит
		{Kind: KindSend, PID: 9, Addr: addr("142.250.1.1"), Bytes: 50, At: at},         // мелкий обмен
		{Kind: KindSend, PID: 9, Addr: addr("8.8.8.8"), Bytes: 5_000_000, At: at},      // не сервис
	}, done: make(chan struct{})}
	out, stop := run(t, testDeps(source, hasherWith([]byte("x"), nil)))

	time.Sleep(200 * time.Millisecond)
	stop()

	if got := out.snapshot(); len(got) != 0 {
		t.Fatalf("событий быть не должно: %v", got)
	}
}

func TestVanishedFileProducesNoEvent(t *testing.T) {
	source := &scriptSource{feed: uploadFeed(`C:\Users\a\Documents\gone.pdf`, 2000), done: make(chan struct{})}
	deps := testDeps(source, hasherWith(nil, errNotExist))
	out, stop := run(t, deps)

	time.Sleep(200 * time.Millisecond)
	stop()

	if got := out.snapshot(); len(got) != 0 {
		t.Fatalf("исчезнувший файл не должен давать событие: %v", got)
	}
}

func TestLockedFileStillReportsWithoutArtifact(t *testing.T) {
	source := &scriptSource{feed: uploadFeed(`C:\Users\a\Documents\busy.pdf`, 2000), done: make(chan struct{})}
	deps := testDeps(source, hasherWith(nil, errPermission))
	out, stop := run(t, deps)
	defer stop()

	got := out.waitFor(t, 1)

	if got[0].Artifact != nil || got[0].Labels["hash"] != "unavailable" {
		t.Fatalf("артефакта нет, причина в labels: %+v %v", got[0].Artifact, got[0].Labels)
	}
}

func TestUnavailableSourceIsReportedAndDoesNotCrash(t *testing.T) {
	source := &scriptSource{err: errors.New("Access is denied"), done: make(chan struct{})}
	out, stop := run(t, testDeps(source, hasherWith(nil, nil)))
	defer stop()

	got := out.waitFor(t, 1)

	if got[0].Channel != events.ChannelAgent || got[0].Action != "netupload_unavailable" {
		t.Fatalf("%s/%s", got[0].Channel, got[0].Action)
	}
	if got[0].Subject["component"] != "netupload" {
		t.Fatalf("subject = %v", got[0].Subject)
	}
}

// Детерминированная проверка переполнения: потребителя нет, очередь не
// разбирается, лишнее считается потерянным.
func TestOverflowIsCounted(t *testing.T) {
	c := New(testDeps(&scriptSource{done: make(chan struct{})}, hasherWith(nil, nil)))
	in := make(chan Event, eventQueue)

	for i := 0; i < eventQueue+100; i++ {
		c.enqueue(in, Event{Kind: KindDNS})
	}

	if got := c.dropped.Load(); got != 100 {
		t.Fatalf("dropped = %d, ждали 100", got)
	}
	if len(in) != eventQueue {
		t.Fatalf("в очереди %d, ждали %d", len(in), eventQueue)
	}
}

func TestDroppedCountIsReported(t *testing.T) {
	deps := testDeps(&scriptSource{done: make(chan struct{})}, hasherWith(nil, nil))
	deps.ReportEvery = 20 * time.Millisecond
	c := New(deps)
	c.dropped.Add(7)
	out, stop := runCollector(t, c)
	defer stop()

	got := out.waitFor(t, 1)

	if got[0].Channel != events.ChannelAgent || got[0].Action != "netupload_dropped" {
		t.Fatalf("%s/%s", got[0].Channel, got[0].Action)
	}
	if count, _ := got[0].Subject["count"].(uint64); count != 7 {
		t.Fatalf("subject = %v", got[0].Subject)
	}
}
