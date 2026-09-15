package runner_test

import (
	"context"
	"encoding/json"
	"errors"
	"math/rand"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/runner"
	"github.com/barysguard/agent/internal/transport"
)

// newAgent собирает агента поверх заданного обработчика HTTP.
func newAgent(t *testing.T, handler http.Handler, state config.State) (*runner.Agent, config.Layout) {
	t.Helper()
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

	agent, err := runner.New(runner.Options{
		ServerURL:    server.URL,
		AgentVersion: "0.1.0",
		Layout:       layout,
		Guard:        guard,
		Client:       client,
		Random:       rand.NewSource(1),
	})
	if err != nil {
		t.Fatalf("runner.New: %v", err)
	}
	return agent, layout
}

func TestRunOnceAppliesServerInterval(t *testing.T) {
	agent, _ := newAgent(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(transport.HeartbeatResponse{
			ServerTime:               time.Now().UTC(),
			ConfigVersion:            0,
			HeartbeatIntervalSeconds: 60,
			Commands:                 []transport.QueuedCommand{},
		})
	}), config.State{ConfigVersion: 0})

	pause, err := agent.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}

	// Интервал сервера плюс джиттер до ±10%.
	if pause < 54*time.Second || pause > 66*time.Second {
		t.Fatalf("пауза %v вне окрестности 60s", pause)
	}
}

func TestConfigIsFetchedWhenVersionDiffers(t *testing.T) {
	var configRequests atomic.Int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/gateway/v1/heartbeat":
			json.NewEncoder(w).Encode(transport.HeartbeatResponse{
				ServerTime:               time.Now().UTC(),
				ConfigVersion:            99,
				HeartbeatIntervalSeconds: 30,
				Commands:                 []transport.QueuedCommand{},
			})
		case "/gateway/v1/config":
			configRequests.Add(1)
			w.Header().Set("ETag", `"99"`)
			json.NewEncoder(w).Encode(transport.ConfigResponse{
				Version:  99,
				Document: map[string]any{"logging": map[string]any{"level": "debug"}},
			})
		}
	})

	agent, layout := newAgent(t, handler, config.State{ConfigVersion: 1})

	if _, err := agent.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if configRequests.Load() != 1 {
		t.Fatalf("запросов конфигурации: %d, ожидался один", configRequests.Load())
	}

	// Версия сохранена — второй проход за документом не пойдёт.
	if _, err := agent.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce повторно: %v", err)
	}
	if configRequests.Load() != 1 {
		t.Fatalf("документ запрошен повторно при совпавшей версии: %d", configRequests.Load())
	}

	state, err := config.LoadState(layout)
	if err != nil {
		t.Fatalf("LoadState: %v", err)
	}
	if state.ConfigVersion != 99 {
		t.Fatalf("версия в состоянии = %d", state.ConfigVersion)
	}
}

func TestCommandIsExecutedAndItsResultSent(t *testing.T) {
	var resultBody atomic.Value
	delivered := false
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/gateway/v1/heartbeat":
			commands := []transport.QueuedCommand{}
			if !delivered {
				delivered = true
				commands = append(commands, transport.QueuedCommand{
					ID: "c-1", Type: "ping", Payload: map[string]any{},
					ExpiresAt: time.Now().Add(time.Hour),
				})
			}
			json.NewEncoder(w).Encode(transport.HeartbeatResponse{
				ServerTime:               time.Now().UTC(),
				HeartbeatIntervalSeconds: 30,
				Commands:                 commands,
			})
		case "/gateway/v1/commands/c-1/result":
			var body transport.CommandResultRequest
			json.NewDecoder(r.Body).Decode(&body)
			resultBody.Store(body)
			w.WriteHeader(http.StatusAccepted)
		}
	})

	agent, _ := newAgent(t, handler, config.State{})
	if _, err := agent.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}

	stored, ok := resultBody.Load().(transport.CommandResultRequest)
	if !ok {
		t.Fatal("результат команды не отправлен")
	}
	if stored.Status != transport.StatusDone || stored.Result["pong"] != true {
		t.Fatalf("результат = %+v", stored)
	}
}

func TestUndeliveredResultIsRetriedOnTheNextPass(t *testing.T) {
	var attempts atomic.Int32
	delivered := false
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/gateway/v1/heartbeat":
			commands := []transport.QueuedCommand{}
			if !delivered {
				delivered = true
				commands = append(commands, transport.QueuedCommand{
					ID: "c-2", Type: "ping", Payload: map[string]any{},
					ExpiresAt: time.Now().Add(time.Hour),
				})
			}
			json.NewEncoder(w).Encode(transport.HeartbeatResponse{
				ServerTime:               time.Now().UTC(),
				HeartbeatIntervalSeconds: 30,
				Commands:                 commands,
			})
		case "/gateway/v1/commands/c-2/result":
			// Первая попытка обрывается, вторая обязана состояться.
			if attempts.Add(1) == 1 {
				w.WriteHeader(http.StatusBadGateway)
				return
			}
			w.WriteHeader(http.StatusAccepted)
		}
	})

	agent, _ := newAgent(t, handler, config.State{})
	agent.RunOnce(context.Background())
	agent.RunOnce(context.Background())

	if attempts.Load() < 2 {
		t.Fatalf("повтор не состоялся, попыток: %d", attempts.Load())
	}
}

func TestForbiddenStopsTheAgent(t *testing.T) {
	// Отзыв сертификата обязан прекращать работу, а не порождать вечные повторы.
	agent, _ := newAgent(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}), config.State{})

	_, err := agent.RunOnce(context.Background())
	if err == nil {
		t.Fatal("ожидалась ошибка на 403")
	}
	if !errors.Is(err, runner.ErrRevoked) {
		t.Fatalf("403 обязан опознаваться как отзыв: %v", err)
	}
}

func TestServerErrorProducesBackoffNotFailure(t *testing.T) {
	agent, _ := newAgent(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}), config.State{})

	pause, err := agent.RunOnce(context.Background())
	if err == nil {
		t.Fatal("ожидалась ошибка на 500")
	}
	if errors.Is(err, runner.ErrRevoked) {
		t.Fatal("500 — это не отзыв сертификата")
	}
	// Полный джиттер вправе вернуть и ноль — это не дефект, а его смысл.
	if pause < 0 || pause > runner.DefaultBackoffMax {
		t.Fatalf("пауза отката %v вне разумных пределов", pause)
	}
}

func TestRetryAfterOverridesBackoff(t *testing.T) {
	agent, _ := newAgent(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Retry-After", "42")
		w.WriteHeader(http.StatusTooManyRequests)
	}), config.State{})

	pause, _ := agent.RunOnce(context.Background())
	if pause != 42*time.Second {
		t.Fatalf("пауза %v, сервер просил 42s", pause)
	}
}
