// Package hostfacts собирает сведения о хосте для регистрации агента.
package hostfacts

import (
	"fmt"
	"os"
	"runtime"

	"github.com/barysguard/agent/internal/platform"
)

// Пределы взяты из схемы HostFacts в api/gateway-v1.yaml. Превышение
// любого из них сервер отвергает целиком, поэтому усечение выполняется здесь.
const (
	maxMachineID    = 255
	maxHostname     = 255
	maxOS           = 32
	maxOSVersion    = 128
	maxArch         = 32
	maxAgentVersion = 32
)

// Facts соответствует схеме HostFacts контракта.
type Facts struct {
	MachineID    string `json:"machine_id"`
	Hostname     string `json:"hostname"`
	OS           string `json:"os"`
	OSVersion    string `json:"os_version"`
	Arch         string `json:"arch"`
	AgentVersion string `json:"agent_version"`
}

func truncate(value string, limit int) string {
	if len(value) <= limit {
		return value
	}
	return value[:limit]
}

// Collect собирает факты хоста. Отсутствие machine_id — ошибка, а не повод
// подставить случайное значение.
func Collect(guard platform.Guard, agentVersion string) (Facts, error) {
	machineID, err := guard.MachineID()
	if err != nil {
		return Facts{}, fmt.Errorf("machine_id недоступен: %w", err)
	}

	hostname, err := os.Hostname()
	if err != nil {
		return Facts{}, fmt.Errorf("hostname недоступен: %w", err)
	}

	// Версия ОС информативна, но не критична: без неё регистрация
	// всё равно должна состояться.
	osVersion, err := guard.OSVersion()
	if err != nil {
		osVersion = "unknown"
	}

	return Facts{
		MachineID:    truncate(machineID, maxMachineID),
		Hostname:     truncate(hostname, maxHostname),
		OS:           truncate(runtime.GOOS, maxOS),
		OSVersion:    truncate(osVersion, maxOSVersion),
		Arch:         truncate(runtime.GOARCH, maxArch),
		AgentVersion: truncate(agentVersion, maxAgentVersion),
	}, nil
}
