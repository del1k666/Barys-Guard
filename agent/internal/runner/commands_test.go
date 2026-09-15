package runner_test

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/runner"
	"github.com/barysguard/agent/internal/transport"
)

func testDispatcher() runner.Dispatcher {
	return runner.NewDispatcher(
		func(context.Context) (int, error) { return 21, nil },
		func() runner.Diagnostics {
			return runner.Diagnostics{
				AgentVersion:    "0.1.0",
				StartedAt:       time.Now().Add(-90 * time.Second),
				CertNotAfter:    time.Now().Add(80 * 24 * time.Hour),
				LastHeartbeatAt: time.Now(),
				ConfigVersion:   21,
			}
		},
	)
}

func TestPingAnswersPong(t *testing.T) {
	result := testDispatcher().Execute(context.Background(), transport.QueuedCommand{
		ID: "c-1", Type: "ping", Payload: map[string]any{},
	})

	if result.Status != transport.StatusDone {
		t.Fatalf("status = %q", result.Status)
	}
	if result.Result["pong"] != true {
		t.Fatalf("результат = %+v", result.Result)
	}
}

func TestRefreshConfigReportsVersion(t *testing.T) {
	result := testDispatcher().Execute(context.Background(), transport.QueuedCommand{
		ID: "c-2", Type: "refresh_config", Payload: map[string]any{},
	})

	if result.Status != transport.StatusDone {
		t.Fatalf("status = %q, тело %+v", result.Status, result.Result)
	}
	if result.Result["config_version"] != 21 {
		t.Fatalf("config_version = %v", result.Result["config_version"])
	}
}

func TestCollectDiagnosticsReportsUptimeAndCertificate(t *testing.T) {
	result := testDispatcher().Execute(context.Background(), transport.QueuedCommand{
		ID: "c-3", Type: "collect_diagnostics", Payload: map[string]any{},
	})

	if result.Status != transport.StatusDone {
		t.Fatalf("status = %q", result.Status)
	}
	for _, key := range []string{"agent_version", "os", "arch", "uptime_seconds", "cert_not_after", "config_version"} {
		if _, ok := result.Result[key]; !ok {
			t.Errorf("в диагностике нет поля %q", key)
		}
	}
}

func TestUnknownCommandFailsWithoutPanicking(t *testing.T) {
	// Сервер новее агента вправе прислать команду, о которой агент не знает.
	result := testDispatcher().Execute(context.Background(), transport.QueuedCommand{
		ID: "c-4", Type: "self_destruct", Payload: map[string]any{},
	})

	if result.Status != transport.StatusFailed {
		t.Fatalf("status = %q, ожидался failed", result.Status)
	}
	if !strings.Contains(result.Result["error"].(string), "self_destruct") {
		t.Fatalf("ошибка обязана называть тип команды: %+v", result.Result)
	}
}

func TestFailingHandlerBecomesFailedResult(t *testing.T) {
	dispatcher := runner.Dispatcher{
		"boom": func(context.Context, map[string]any) (map[string]any, error) {
			return nil, errors.New("диск недоступен")
		},
	}

	result := dispatcher.Execute(context.Background(), transport.QueuedCommand{ID: "c-5", Type: "boom"})

	if result.Status != transport.StatusFailed {
		t.Fatalf("status = %q", result.Status)
	}
	// Молчание оставило бы команду висеть на сервере до истечения TTL.
	if result.Result["error"] != "диск недоступен" {
		t.Fatalf("текст ошибки потерян: %+v", result.Result)
	}
}

func TestOversizedResultIsTruncated(t *testing.T) {
	dispatcher := runner.Dispatcher{
		"flood": func(context.Context, map[string]any) (map[string]any, error) {
			return map[string]any{"blob": strings.Repeat("x", runner.MaxResultBytes*2)}, nil
		},
	}

	result := dispatcher.Execute(context.Background(), transport.QueuedCommand{ID: "c-6", Type: "flood"})

	// Сервер отвергает результат крупнее MAX_RESULT_BYTES целиком;
	// диагностика не является каналом передачи артефактов.
	if result.Result["truncated"] != true {
		t.Fatalf("крупный результат обязан быть усечён: %+v", result.Result)
	}
	if _, ok := result.Result["blob"]; ok {
		t.Fatal("после усечения исходное поле остаться не должно")
	}
}
