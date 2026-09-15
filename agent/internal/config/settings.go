package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"

	"github.com/barysguard/agent/internal/platform"
)

// Settings — то, что задаёт оператор или установщик.
type Settings struct {
	ServerURL string `json:"server_url"`
	LogLevel  string `json:"log_level"`
}

// State — то, что агент заработал сам.
//
// Документ конфигурации хранится вместе с версией: без него заголовок
// If-None-Match бессмысленен, и после каждого перезапуска агент тянул бы
// полный документ заново.
type State struct {
	AgentID       string         `json:"agent_id"`
	ConfigVersion int            `json:"config_version"`
	Document      map[string]any `json:"document,omitempty"`
}

func readJSON(path string, target any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(raw, target); err != nil {
		return fmt.Errorf("разбор %s: %w", path, err)
	}
	return nil
}

func writeJSON(path string, value any, guard platform.Guard) error {
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return WriteAtomic(path, raw, guard)
}

func LoadSettings(layout Layout) (Settings, error) {
	var settings Settings
	if err := readJSON(layout.SettingsPath(), &settings); err != nil {
		return Settings{}, err
	}
	if settings.ServerURL == "" {
		return Settings{}, fmt.Errorf("%s не задаёт server_url", layout.SettingsPath())
	}
	return settings, nil
}

func SaveSettings(layout Layout, settings Settings, guard platform.Guard) error {
	return writeJSON(layout.SettingsPath(), settings, guard)
}

// LoadState не считает отсутствие файла ошибкой: до регистрации его нет.
func LoadState(layout Layout) (State, error) {
	var state State
	err := readJSON(layout.StatePath(), &state)
	if errors.Is(err, os.ErrNotExist) {
		return State{}, nil
	}
	if err != nil {
		return State{}, err
	}
	return state, nil
}

func SaveState(layout Layout, state State, guard platform.Guard) error {
	return writeJSON(layout.StatePath(), state, guard)
}
