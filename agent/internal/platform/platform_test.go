package platform_test

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/barysguard/agent/internal/platform"
)

func TestSecureDirCreatesDirectory(t *testing.T) {
	guard := platform.New()
	dir := filepath.Join(t.TempDir(), "pki")

	if err := guard.SecureDir(dir); err != nil {
		t.Fatalf("SecureDir: %v", err)
	}

	info, err := os.Stat(dir)
	if err != nil {
		t.Fatalf("Stat: %v", err)
	}
	if !info.IsDir() {
		t.Fatal("ожидался каталог")
	}
}

func TestSecureFileSurvivesVerification(t *testing.T) {
	guard := platform.New()
	dir := t.TempDir()
	path := filepath.Join(dir, "agent.key")
	if err := os.WriteFile(path, []byte("key"), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	if err := guard.SecureFile(path); err != nil {
		t.Fatalf("SecureFile: %v", err)
	}
	if err := guard.VerifySecure(path); err != nil {
		t.Fatalf("после SecureFile проверка обязана проходить: %v", err)
	}
}

func TestVerifySecureRejectsWorldReadableKey(t *testing.T) {
	// Ключ, который прочитал кто угодно, уже не доказывает личность.
	if runtime.GOOS == "windows" {
		t.Skip("режим доступа Unix неприменим к Windows")
	}
	guard := platform.New()
	path := filepath.Join(t.TempDir(), "agent.key")
	if err := os.WriteFile(path, []byte("key"), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	err := guard.VerifySecure(path)
	if err == nil {
		t.Fatal("ожидался отказ на права 0644")
	}
	if !strings.Contains(err.Error(), "0644") {
		t.Fatalf("ошибка обязана называть фактические права, получено: %v", err)
	}
}

func TestMachineIDIsStableAndNotEmpty(t *testing.T) {
	guard := platform.New()

	first, err := guard.MachineID()
	if err != nil {
		t.Fatalf("MachineID: %v", err)
	}
	if first == "" {
		t.Fatal("machine_id пуст")
	}

	second, _ := guard.MachineID()
	if first != second {
		// Меняющийся идентификатор превратил бы один хост в россыпь агентов.
		t.Fatalf("machine_id нестабилен: %q затем %q", first, second)
	}
}

func TestOSVersionIsNotEmpty(t *testing.T) {
	version, err := platform.New().OSVersion()
	if err != nil {
		t.Fatalf("OSVersion: %v", err)
	}
	if version == "" {
		t.Fatal("версия ОС пуста")
	}
}
