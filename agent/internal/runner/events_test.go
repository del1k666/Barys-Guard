package runner_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"math/rand"
	"net/http"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/buffer"
	"github.com/barysguard/agent/internal/collectors/lifecycle"
	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/runner"
	"github.com/barysguard/agent/internal/transport"
)

// fakeGateway — шлюз, принимающий heartbeat, конфигурацию и события.
type fakeGateway struct {
	mu             sync.Mutex
	down           atomic.Bool
	maxLines       int // при > 0 пакеты длиннее отвергаются кодом 413
	rejectAll      bool
	configVersion  int
	configDocument map[string]any
	ids            map[string]int
	heartbeat      transport.HeartbeatRequest
}

func (g *fakeGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/gateway/v1/heartbeat":
		g.mu.Lock()
		json.NewDecoder(r.Body).Decode(&g.heartbeat)
		version := g.configVersion
		g.mu.Unlock()
		json.NewEncoder(w).Encode(transport.HeartbeatResponse{
			ServerTime: time.Now().UTC(), ConfigVersion: version, HeartbeatIntervalSeconds: 30,
			Commands: []transport.QueuedCommand{},
		})
	case "/gateway/v1/config":
		w.Header().Set("ETag", `"99"`)
		document := g.configDocument
		if document == nil {
			document = map[string]any{}
		}
		json.NewEncoder(w).Encode(transport.ConfigResponse{Version: 99, Document: document})
	case "/gateway/v1/events":
		if g.down.Load() {
			http.Error(w, "недоступен", http.StatusServiceUnavailable)
			return
		}
		raw := new(bytes.Buffer)
		raw.ReadFrom(r.Body)
		lines := strings.Split(strings.TrimSpace(raw.String()), "\n")
		if g.maxLines > 0 && len(lines) > g.maxLines {
			http.Error(w, "велик", http.StatusRequestEntityTooLarge)
			return
		}
		g.mu.Lock()
		result := transport.EventsResult{Rejected: []transport.RejectedEvent{}}
		for i, line := range lines {
			if g.rejectAll {
				result.Rejected = append(result.Rejected, transport.RejectedEvent{Line: i + 1, Reason: "invalid_event"})
				continue
			}
			var env events.Envelope
			json.Unmarshal([]byte(line), &env)
			if g.ids[env.EventID] > 0 {
				result.Duplicates++
			} else {
				result.Accepted++
			}
			g.ids[env.EventID]++
		}
		g.mu.Unlock()
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(result)
	}
}

func (g *fakeGateway) received() map[string]int {
	g.mu.Lock()
	defer g.mu.Unlock()
	copyOf := map[string]int{}
	for k, v := range g.ids {
		copyOf[k] = v
	}
	return copyOf
}

// newEventAgent собирает агента с настоящим буфером поверх fakeGateway.
func newEventAgent(t *testing.T, gateway *fakeGateway, state config.State) (*runner.Agent, *buffer.Buffer) {
	t.Helper()
	gateway.ids = map[string]int{}
	return newEventAgentWithHandler(t, gateway, nil, state)
}

// newEventAgentWithHandler — то же поверх произвольного обработчика и со сборщиками.
func newEventAgentWithHandler(
	t *testing.T, handler http.Handler, collectors []events.Collector, states ...config.State,
) (*runner.Agent, *buffer.Buffer) {
	t.Helper()
	state := config.State{}
	if len(states) > 0 {
		state = states[0]
	}

	ca := newRunnerCA(t)
	server := newRunnerTLSServer(t, ca, handler)
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()
	if err := config.SaveState(layout, state, guard); err != nil {
		t.Fatalf("SaveState: %v", err)
	}
	client, err := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	if err != nil {
		t.Fatalf("NewMutual: %v", err)
	}

	buf, err := buffer.Open(buffer.Options{
		Path:     filepath.Join(t.TempDir(), "events.db"),
		Key:      bytes.Repeat([]byte{7}, 32),
		MaxBytes: 1 << 30,
		MaxAge:   time.Hour * 24 * 7,
	})
	if err != nil {
		t.Fatalf("buffer.Open: %v", err)
	}
	t.Cleanup(func() { buf.Close() })

	agent, err := runner.New(runner.Options{
		ServerURL: server.URL, AgentVersion: "0.1.0", Layout: layout, Guard: guard,
		Client: client, Random: rand.NewSource(1), Buffer: buf, Collectors: collectors,
	})
	if err != nil {
		t.Fatalf("runner.New: %v", err)
	}
	return agent, buf
}

func appendEvents(t *testing.T, buf *buffer.Buffer, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		env, err := events.NewEnvelope(events.ChannelAgent, "start", events.SeverityInfo, nil)
		if err != nil {
			t.Fatal(err)
		}
		if err := buf.Append(env); err != nil {
			t.Fatal(err)
		}
	}
}

func TestBufferedEventsAreSentAndAcknowledged(t *testing.T) {
	gateway := &fakeGateway{}
	agent, buf := newEventAgent(t, gateway, config.State{})
	appendEvents(t, buf, 3)

	if _, err := agent.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}

	if len(gateway.received()) != 3 {
		t.Fatalf("сервер получил %d событий", len(gateway.received()))
	}
	if count, _ := buf.Stats(); count != 0 {
		t.Fatalf("в буфере осталось %d после подтверждения", count)
	}
}

func TestHeartbeatReportsBufferState(t *testing.T) {
	gateway := &fakeGateway{}
	gateway.down.Store(true) // события не уйдут, буфер останется
	agent, buf := newEventAgent(t, gateway, config.State{})
	appendEvents(t, buf, 2)

	if _, err := agent.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}

	gateway.mu.Lock()
	defer gateway.mu.Unlock()
	if gateway.heartbeat.BufferedEvents != 2 || gateway.heartbeat.BufferBytes <= 0 {
		t.Fatalf("heartbeat: %+v", gateway.heartbeat)
	}
}

func TestOutageKeepsEventsAndRecoveryDeliversEachExactlyOnce(t *testing.T) {
	gateway := &fakeGateway{}
	gateway.down.Store(true)
	agent, buf := newEventAgent(t, gateway, config.State{})

	// События идут через очередь, как от настоящего сборщика.
	stop := agent.StartEvents(context.Background())
	for i := 0; i < 25; i++ {
		env, _ := events.NewEnvelope(events.ChannelAgent, "start", events.SeverityInfo, nil)
		agent.Emit(env)
	}
	stop() // очередь доработана: всё в буфере

	for pass := 0; pass < 3; pass++ {
		agent.RunOnce(context.Background())
	}
	if count, _ := buf.Stats(); count != 25 {
		t.Fatalf("во время обрыва в буфере %d, ожидалось 25", count)
	}
	if len(gateway.received()) != 0 {
		t.Fatal("события дошли при недоступном сервере")
	}

	gateway.down.Store(false)
	agent.RunOnce(context.Background())

	received := gateway.received()
	if len(received) != 25 {
		t.Fatalf("после восстановления доставлено %d из 25", len(received))
	}
	for id, times := range received {
		if times != 1 {
			t.Errorf("событие %s доставлено %d раз", id, times)
		}
	}
	if count, _ := buf.Stats(); count != 0 {
		t.Fatalf("буфер не пуст: %d", count)
	}
}

func TestRejectedEventsAreAcknowledgedToo(t *testing.T) {
	// Отклонённое событие не станет верным от повторной отправки;
	// иначе оно навсегда заблокировало бы голову очереди.
	gateway := &fakeGateway{rejectAll: true}
	agent, buf := newEventAgent(t, gateway, config.State{})
	appendEvents(t, buf, 2)

	agent.RunOnce(context.Background())

	if count, _ := buf.Stats(); count != 0 {
		t.Fatalf("отклонённые остались в буфере: %d", count)
	}
}

func TestEntityTooLargeHalvesTheBatch(t *testing.T) {
	gateway := &fakeGateway{maxLines: 2}
	agent, buf := newEventAgent(t, gateway, config.State{})
	appendEvents(t, buf, 5)

	agent.RunOnce(context.Background())

	if len(gateway.received()) != 5 {
		t.Fatalf("доставлено %d из 5", len(gateway.received()))
	}
	if count, _ := buf.Stats(); count != 0 {
		t.Fatalf("в буфере осталось %d", count)
	}
}

func TestOnePassSendsAtMostTwentyBatches(t *testing.T) {
	gateway := &fakeGateway{}
	state := config.State{Document: map[string]any{
		"transport": map[string]any{"event_batch_max": float64(1)},
	}}
	agent, buf := newEventAgent(t, gateway, state)
	appendEvents(t, buf, 25)

	agent.RunOnce(context.Background())

	if len(gateway.received()) != runner.MaxEventBatchesPerPass {
		t.Fatalf("за проход доставлено %d, предел %d", len(gateway.received()), runner.MaxEventBatchesPerPass)
	}
	if count, _ := buf.Stats(); count != 25-runner.MaxEventBatchesPerPass {
		t.Fatalf("в буфере осталось %d", count)
	}
}

func TestConfigChangeEmitsConfigAppliedEvent(t *testing.T) {
	gateway := &fakeGateway{configVersion: 99}
	agent, buf := newEventAgent(t, gateway, config.State{ConfigVersion: 1})
	gateway.down.Store(true) // событие должно остаться в буфере, чтобы его можно было прочитать

	stop := agent.StartEvents(context.Background())
	agent.RunOnce(context.Background()) // версия 99 ≠ 1: документ забирается
	stop()                              // очередь доработана, config_applied в буфере

	batch, err := buf.NextBatch(10, 1<<20)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, line := range batch.Lines {
		if strings.Contains(string(line), `"action":"config_applied"`) {
			found = true
		}
	}
	if !found {
		t.Fatalf("config_applied не найден среди %d событий", batch.Len())
	}
}

// Отозванный агент обязан завершиться кодом отзыва даже при живых сборщиках:
// lifecycle ждёт отмены контекста, и без явной остановки сборщиков Run
// зависал бы навсегда, а main до os.Exit(exitRevoked) не доходил.
func TestRevokedAgentExitsEvenWithRunningCollectors(t *testing.T) {
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "отозван", http.StatusForbidden)
	})
	agent, _ := newEventAgentWithHandler(t, handler, []events.Collector{lifecycle.New("test")})

	done := make(chan error, 1)
	go func() { done <- agent.Run(context.Background()) }()

	select {
	case err := <-done:
		if !errors.Is(err, runner.ErrRevoked) {
			t.Fatalf("Run вернул %v, ожидался ErrRevoked", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Run завис: сборщики не остановлены после отзыва")
	}
}

// probeCollector считает запуски и останавливается по отмене своего контекста.
type probeCollector struct {
	starts, stops *atomic.Int32
}

func (p probeCollector) Name() string { return "probe" }
func (p probeCollector) Run(ctx context.Context, _ func(events.Envelope)) error {
	p.starts.Add(1)
	<-ctx.Done()
	p.stops.Add(1)
	return nil
}

type factoryProbe struct {
	calls         atomic.Int32
	starts, stops atomic.Int32
	mu            sync.Mutex
	docs          []map[string]any
}

func (f *factoryProbe) build(doc map[string]any) []events.Collector {
	f.calls.Add(1)
	f.mu.Lock()
	f.docs = append(f.docs, doc)
	f.mu.Unlock()
	return []events.Collector{probeCollector{starts: &f.starts, stops: &f.stops}}
}

func waitFor(t *testing.T, what string, cond func() bool) {
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

func newFactoryAgent(t *testing.T, gateway *fakeGateway, state config.State, probe *factoryProbe) *runner.Agent {
	t.Helper()
	gateway.ids = map[string]int{}
	ca := newRunnerCA(t)
	server := newRunnerTLSServer(t, ca, gateway)
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()
	if err := config.SaveState(layout, state, guard); err != nil {
		t.Fatal(err)
	}
	client, err := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	if err != nil {
		t.Fatal(err)
	}
	buf, err := buffer.Open(buffer.Options{
		Path: filepath.Join(t.TempDir(), "events.db"), Key: bytes.Repeat([]byte{7}, 32),
		MaxBytes: 1 << 30, MaxAge: 24 * time.Hour,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { buf.Close() })

	agent, err := runner.New(runner.Options{
		ServerURL: server.URL, AgentVersion: "0.1.0", Layout: layout, Guard: guard, Client: client,
		Random: rand.NewSource(1), Buffer: buf, CollectorFactory: probe.build,
	})
	if err != nil {
		t.Fatal(err)
	}
	return agent
}

func TestFactoryCollectorsStartWithTheSavedDocument(t *testing.T) {
	probe := &factoryProbe{}
	state := config.State{ConfigVersion: 5, Document: map[string]any{"collectors": map[string]any{"usb": map[string]any{"enabled": false}}}}
	agent := newFactoryAgent(t, &fakeGateway{configVersion: 5}, state, probe)

	stop := agent.StartEvents(context.Background())
	waitFor(t, "сборщик запущен", func() bool { return probe.starts.Load() == 1 })
	stop()

	if probe.stops.Load() != 1 {
		t.Fatal("сборщик не остановлен вместе с агентом")
	}
	if usb, _ := probe.docs[0]["collectors"].(map[string]any)["usb"].(map[string]any); usb["enabled"] != false {
		t.Fatalf("фабрика получила не сохранённый документ: %+v", probe.docs[0])
	}
}

// Смена версии без смены раздела collectors не должна перезапускать сборщики:
// повторный старт опроса томов продублировал бы usb/mount для вставленных флешек.
func TestConfigChangeOutsideCollectorsDoesNotRestartThem(t *testing.T) {
	probe := &factoryProbe{}
	gateway := &fakeGateway{configVersion: 99, configDocument: map[string]any{"logging": map[string]any{"level": "debug"}}}
	agent := newFactoryAgent(t, gateway, config.State{ConfigVersion: 1}, probe)

	stop := agent.StartEvents(context.Background())
	waitFor(t, "первый запуск", func() bool { return probe.starts.Load() == 1 })
	agent.RunOnce(context.Background()) // версия 99 ≠ 1: документ сменился, но collectors нет
	time.Sleep(200 * time.Millisecond)
	stop()

	if probe.calls.Load() != 1 || probe.starts.Load() != 1 {
		t.Fatalf("фабрика вызвана %d раз, запусков %d: перезапуска быть не должно", probe.calls.Load(), probe.starts.Load())
	}
}

func TestChangedCollectorsSectionRestartsTheGroupWithTheNewDocument(t *testing.T) {
	probe := &factoryProbe{}
	newCollectors := map[string]any{"usb": map[string]any{"enabled": false}}
	gateway := &fakeGateway{configVersion: 99, configDocument: map[string]any{"collectors": newCollectors}}
	agent := newFactoryAgent(t, gateway, config.State{ConfigVersion: 1}, probe)

	stop := agent.StartEvents(context.Background())
	waitFor(t, "первый запуск", func() bool { return probe.starts.Load() == 1 })
	agent.RunOnce(context.Background())
	waitFor(t, "перезапуск группы", func() bool { return probe.starts.Load() == 2 })
	stop()

	if probe.stops.Load() != 2 {
		t.Fatalf("остановок %d: старая группа обязана остановиться до новой", probe.stops.Load())
	}
	probe.mu.Lock()
	defer probe.mu.Unlock()
	if got := probe.docs[1]["collectors"]; !reflect.DeepEqual(got, newCollectors) {
		t.Fatalf("новая группа собрана по старому документу: %+v", got)
	}
}
