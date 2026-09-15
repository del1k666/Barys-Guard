package hostfacts_test

import (
	"errors"
	"runtime"
	"strings"
	"testing"

	"github.com/barysguard/agent/internal/hostfacts"
)

type stubGuard struct {
	machineID string
	osVersion string
	err       error
}

func (s stubGuard) SecureDir(string) error     { return nil }
func (s stubGuard) SecureFile(string) error    { return nil }
func (s stubGuard) VerifySecure(string) error  { return nil }
func (s stubGuard) MachineID() (string, error) { return s.machineID, s.err }
func (s stubGuard) OSVersion() (string, error) { return s.osVersion, nil }

func TestCollectFillsEveryContractField(t *testing.T) {
	facts, err := hostfacts.Collect(stubGuard{machineID: "abc", osVersion: "Ubuntu 24.04"}, "0.1.0")
	if err != nil {
		t.Fatalf("Collect: %v", err)
	}

	if facts.MachineID != "abc" {
		t.Fatalf("machine_id = %q", facts.MachineID)
	}
	if facts.OS != runtime.GOOS {
		t.Fatalf("os = %q, ожидалось %q", facts.OS, runtime.GOOS)
	}
	if facts.Arch != runtime.GOARCH {
		t.Fatalf("arch = %q", facts.Arch)
	}
	if facts.AgentVersion != "0.1.0" {
		t.Fatalf("agent_version = %q", facts.AgentVersion)
	}
	if facts.Hostname == "" {
		t.Fatal("hostname пуст")
	}
}

func TestCollectFailsWithoutMachineID(t *testing.T) {
	// Выдуманный идентификатор превратил бы один хост в россыпь
	// агентов-призраков: сервер отсеивает дубли именно по нему.
	_, err := hostfacts.Collect(stubGuard{err: errors.New("нет файла")}, "0.1.0")
	if err == nil {
		t.Fatal("ожидалась ошибка при недоступном machine_id")
	}
}

func TestLongOSVersionIsTruncatedToContractLimit(t *testing.T) {
	facts, err := hostfacts.Collect(
		stubGuard{machineID: "abc", osVersion: strings.Repeat("x", 500)}, "0.1.0",
	)
	if err != nil {
		t.Fatalf("Collect: %v", err)
	}
	// Контракт задаёт maxLength 128; сервер отверг бы запрос целиком.
	if len(facts.OSVersion) != 128 {
		t.Fatalf("длина os_version = %d, ожидалось 128", len(facts.OSVersion))
	}
}
