package config_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/platform"
)

func TestLayoutPutsKeysUnderPKI(t *testing.T) {
	layout := config.NewLayout("base")

	if got, want := layout.PKIDir(), filepath.Join("base", "pki"); got != want {
		t.Fatalf("PKIDir = %q, ожидалось %q", got, want)
	}
	if got, want := layout.KeyPath(), filepath.Join("base", "pki", "agent.key"); got != want {
		t.Fatalf("KeyPath = %q, ожидалось %q", got, want)
	}
	if got, want := layout.SettingsPath(), filepath.Join("base", "agent.json"); got != want {
		t.Fatalf("SettingsPath = %q, ожидалось %q", got, want)
	}
}

func TestWriteAtomicLeavesNoTemporaryFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state.json")

	if err := config.WriteAtomic(path, []byte(`{"a":1}`), platform.New()); err != nil {
		t.Fatalf("WriteAtomic: %v", err)
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("ReadDir: %v", err)
	}
	if len(entries) != 1 {
		// Временный файл, оставшийся рядом, означает незавершённую запись.
		t.Fatalf("в каталоге %d записей, ожидалась одна", len(entries))
	}
}

func TestWriteAtomicReplacesExistingContent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	guard := platform.New()

	if err := config.WriteAtomic(path, []byte("старое"), guard); err != nil {
		t.Fatalf("WriteAtomic: %v", err)
	}
	if err := config.WriteAtomic(path, []byte("новое"), guard); err != nil {
		t.Fatalf("WriteAtomic повторно: %v", err)
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if string(raw) != "новое" {
		t.Fatalf("содержимое = %q", raw)
	}
}

func TestMissingStateIsEmptyNotError(t *testing.T) {
	// До регистрации файла состояния нет, и это нормальный ход событий.
	state, err := config.LoadState(config.NewLayout(t.TempDir()))
	if err != nil {
		t.Fatalf("LoadState: %v", err)
	}
	if state.AgentID != "" || state.ConfigVersion != 0 {
		t.Fatalf("ожидалось пустое состояние, получено %+v", state)
	}
}

func TestStateSurvivesRoundTrip(t *testing.T) {
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()
	want := config.State{
		AgentID:       "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f",
		ConfigVersion: 42,
		Document:      map[string]any{"logging": map[string]any{"level": "debug"}},
	}

	if err := config.SaveState(layout, want, guard); err != nil {
		t.Fatalf("SaveState: %v", err)
	}
	got, err := config.LoadState(layout)
	if err != nil {
		t.Fatalf("LoadState: %v", err)
	}

	if got.AgentID != want.AgentID || got.ConfigVersion != want.ConfigVersion {
		t.Fatalf("состояние не сохранилось: %+v", got)
	}
	// Документ нужен, чтобы If-None-Match имел смысл после перезапуска.
	logging, ok := got.Document["logging"].(map[string]any)
	if !ok || logging["level"] != "debug" {
		t.Fatalf("документ конфигурации потерян: %+v", got.Document)
	}
}

func TestSettingsSurviveRoundTrip(t *testing.T) {
	layout := config.NewLayout(t.TempDir())

	err := config.SaveSettings(layout, config.Settings{
		ServerURL: "https://dlp.example:8443",
		LogLevel:  "debug",
	}, platform.New())
	if err != nil {
		t.Fatalf("SaveSettings: %v", err)
	}

	got, err := config.LoadSettings(layout)
	if err != nil {
		t.Fatalf("LoadSettings: %v", err)
	}
	if got.ServerURL != "https://dlp.example:8443" || got.LogLevel != "debug" {
		t.Fatalf("настройки = %+v", got)
	}
}
