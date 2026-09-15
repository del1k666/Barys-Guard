package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"runtime"
	"time"

	"github.com/barysguard/agent/internal/transport"
)

// Предел совпадает с MAX_RESULT_BYTES сервера. Более крупный результат
// сервер отвергает целиком, поэтому усечение выполняется здесь.
const MaxResultBytes = 64 * 1024

// Handler исполняет одну команду.
type Handler func(ctx context.Context, payload map[string]any) (map[string]any, error)

// Dispatcher отделён от цикла, чтобы боевые команды подпроекта 2
// добавлялись без правки расписания.
type Dispatcher map[string]Handler

// Diagnostics — снимок состояния агента для команды collect_diagnostics.
type Diagnostics struct {
	AgentVersion    string
	StartedAt       time.Time
	CertNotAfter    time.Time
	LastHeartbeatAt time.Time
	ConfigVersion   int
}

// NewDispatcher собирает набор команд этого плана.
//
// refresh принудительно забирает конфигурацию и отдаёт её версию;
// diagnostics отдаёт снимок состояния. Оба переданы функциями, чтобы
// диспетчер не зависел ни от транспорта, ни от цикла.
func NewDispatcher(
	refresh func(context.Context) (int, error),
	diagnostics func() Diagnostics,
) Dispatcher {
	return Dispatcher{
		"ping": func(context.Context, map[string]any) (map[string]any, error) {
			return map[string]any{
				"pong":       true,
				"agent_time": time.Now().UTC().Format(time.RFC3339),
			}, nil
		},
		"refresh_config": func(ctx context.Context, _ map[string]any) (map[string]any, error) {
			version, err := refresh(ctx)
			if err != nil {
				return nil, err
			}
			return map[string]any{"config_version": version}, nil
		},
		"collect_diagnostics": func(context.Context, map[string]any) (map[string]any, error) {
			snapshot := diagnostics()
			return map[string]any{
				"agent_version":     snapshot.AgentVersion,
				"os":                runtime.GOOS,
				"arch":              runtime.GOARCH,
				"uptime_seconds":    int(time.Since(snapshot.StartedAt).Seconds()),
				"cert_not_after":    snapshot.CertNotAfter.UTC().Format(time.RFC3339),
				"last_heartbeat_at": snapshot.LastHeartbeatAt.UTC().Format(time.RFC3339),
				"config_version":    snapshot.ConfigVersion,
			}, nil
		},
	}
}

// fits сообщает, помещается ли результат в предел сервера.
func fits(result map[string]any) bool {
	raw, err := json.Marshal(result)
	return err == nil && len(raw) <= MaxResultBytes
}

// Execute исполняет команду и всегда возвращает результат, пригодный к отправке.
//
// Неизвестный тип — это failed с внятным текстом, а не падение цикла:
// правило совместимости допускает сервер новее агента.
func (d Dispatcher) Execute(ctx context.Context, command transport.QueuedCommand) transport.CommandResultRequest {
	handler, known := d[command.Type]
	if !known {
		return transport.CommandResultRequest{
			Status: transport.StatusFailed,
			Result: map[string]any{"error": fmt.Sprintf("команда %q агенту неизвестна", command.Type)},
		}
	}

	result, err := handler(ctx, command.Payload)
	if err != nil {
		return transport.CommandResultRequest{
			Status: transport.StatusFailed,
			Result: map[string]any{"error": err.Error()},
		}
	}
	if result == nil {
		result = map[string]any{}
	}
	if !fits(result) {
		result = map[string]any{
			"truncated": true,
			"note":      "результат превысил предел сервера и опущен",
		}
	}
	return transport.CommandResultRequest{Status: transport.StatusDone, Result: result}
}
