// agent/internal/runner/artifacts_test.go
package runner_test

import (
	"bytes"
	"context"
	"math/rand"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/buffer"
	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/runner"
	"github.com/barysguard/agent/internal/transport"
)

type fakeWorker struct {
	mu          sync.Mutex
	configs     []artifacts.Config
	runs, stops atomic.Int32
}

func (f *fakeWorker) SetConfig(cfg artifacts.Config) {
	f.mu.Lock()
	f.configs = append(f.configs, cfg)
	f.mu.Unlock()
}

func (f *fakeWorker) last() (artifacts.Config, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.configs) == 0 {
		return artifacts.Config{}, false
	}
	return f.configs[len(f.configs)-1], true
}

func (f *fakeWorker) Run(ctx context.Context, _ func(events.Envelope)) {
	f.runs.Add(1)
	<-ctx.Done()
	f.stops.Add(1)
}

func newWorkerAgent(t *testing.T, gateway *fakeGateway, state config.State, worker runner.ArtifactWorker) *runner.Agent {
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
		Random: rand.NewSource(1), Buffer: buf, Artifacts: worker,
	})
	if err != nil {
		t.Fatal(err)
	}
	return agent
}

func TestArtifactWorkerStartsWithTheSavedConfigAndStopsWithTheAgent(t *testing.T) {
	worker := &fakeWorker{}
	state := config.State{ConfigVersion: 5, Document: map[string]any{
		"collectors": map[string]any{"artifact": map[string]any{"max_bytes": float64(4321)}},
	}}
	agent := newWorkerAgent(t, &fakeGateway{configVersion: 5}, state, worker)

	stop := agent.StartEvents(context.Background())
	waitFor(t, "воркер запущен", func() bool { return worker.runs.Load() == 1 })
	cfg, ok := worker.last()
	if !ok || cfg.MaxBytes != 4321 {
		t.Fatalf("воркер получил конфигурацию %+v", cfg)
	}
	stop()

	if worker.stops.Load() != 1 {
		t.Fatal("воркер не остановлен вместе с агентом")
	}
}

func TestNewConfigIsAppliedToTheArtifactWorker(t *testing.T) {
	worker := &fakeWorker{}
	gateway := &fakeGateway{configVersion: 99, configDocument: map[string]any{
		"collectors": map[string]any{"artifact": map[string]any{"max_bytes": float64(1234)}},
	}}
	agent := newWorkerAgent(t, gateway, config.State{ConfigVersion: 1}, worker)

	stop := agent.StartEvents(context.Background())
	waitFor(t, "воркер запущен", func() bool { return worker.runs.Load() == 1 })
	agent.RunOnce(context.Background())
	waitFor(t, "новая конфигурация применена", func() bool {
		cfg, ok := worker.last()
		return ok && cfg.MaxBytes == 1234
	})
	stop()
}

func TestAgentWithoutAnArtifactWorkerStillStarts(t *testing.T) {
	agent := newWorkerAgent(t, &fakeGateway{configVersion: 5}, config.State{ConfigVersion: 5}, nil)

	stop := agent.StartEvents(context.Background())
	stop()
}
