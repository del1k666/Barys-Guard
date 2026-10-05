// Package filewatch — сборщик канала file: наблюдение за наблюдаемыми папками
// и внешними томами, склейка уведомлений, хеширование, сопоставление копирования.
package filewatch

import (
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Умолчания совпадают с серверными (services/config.py); агент обязан работать
// и с документом, в котором раздела collectors нет.
const (
	defaultStableMs     = 1500
	defaultMaxWaitMs    = 30_000
	defaultMaxHashBytes = 256 * 1024 * 1024
	defaultMaxPerSecond = 200
)

var (
	defaultPaths   = []string{`%USERS%\Documents`, `%USERS%\Desktop`, `%USERS%\Downloads`}
	defaultExclude = []string{`*\~$*`, `*.tmp`, `*.crdownload`, `*\AppData\*`}
)

type Config struct {
	Enabled            bool
	Paths              []string
	Exclude            []string
	Stable, MaxWait    time.Duration
	MaxHashBytes       int64
	MaxEventsPerSecond int
}

func section(document map[string]any, names ...string) map[string]any {
	current := document
	for _, name := range names {
		next, _ := current[name].(map[string]any)
		if next == nil {
			return nil
		}
		current = next
	}
	return current
}

func number(section map[string]any, key string, fallback float64) float64 {
	if value, ok := section[key].(float64); ok && value > 0 {
		return value
	}
	return fallback
}

func stringList(section map[string]any, key string, fallback []string) []string {
	raw, ok := section[key].([]any)
	if !ok {
		return append([]string(nil), fallback...)
	}
	var out []string
	for _, item := range raw {
		if text, ok := item.(string); ok && text != "" {
			out = append(out, text)
		}
	}
	return out
}

// ConfigFromDocument читает collectors.file_watch. Отсутствующее или
// бессмысленное значение заменяется умолчанием: плохой документ не должен
// оставлять агента без наблюдения. profiles — каталоги профилей для %USERS%,
// dataDir — рабочий каталог агента, который никогда не наблюдается.
func ConfigFromDocument(document map[string]any, profiles []string, dataDir string) Config {
	fw := section(document, "collectors", "file_watch")

	cfg := Config{Enabled: true}
	if enabled, ok := fw["enabled"].(bool); ok {
		cfg.Enabled = enabled
	}
	cfg.Stable = time.Duration(number(fw, "stable_ms", defaultStableMs)) * time.Millisecond
	cfg.MaxWait = time.Duration(number(fw, "max_wait_ms", defaultMaxWaitMs)) * time.Millisecond
	cfg.MaxHashBytes = int64(number(fw, "max_hash_bytes", defaultMaxHashBytes))
	cfg.MaxEventsPerSecond = int(number(fw, "max_events_per_second", defaultMaxPerSecond))
	cfg.Paths = expandPaths(stringList(fw, "paths", defaultPaths), profiles)
	cfg.Exclude = stringList(fw, "exclude", defaultExclude)
	if dataDir != "" {
		cfg.Exclude = append(cfg.Exclude, strings.TrimRight(dataDir, `\/`)+`\*`)
	}
	return cfg
}

func expandPaths(paths, profiles []string) []string {
	const token = "%USERS%"
	var out []string
	for _, path := range paths {
		if !strings.Contains(path, token) {
			out = append(out, path)
			continue
		}
		for _, profile := range profiles {
			out = append(out, strings.Replace(path, token, strings.TrimRight(profile, `\/`), 1))
		}
	}
	return out
}

// ProfileDirs перечисляет профили пользователей в usersRoot, кроме системных.
func ProfileDirs(usersRoot string) []string {
	skip := map[string]bool{"public": true, "default": true, "default user": true, "all users": true}
	entries, err := os.ReadDir(usersRoot)
	if err != nil {
		return nil
	}
	var out []string
	for _, entry := range entries {
		if !entry.IsDir() || skip[strings.ToLower(entry.Name())] {
			continue
		}
		out = append(out, filepath.Join(usersRoot, entry.Name()))
	}
	return out
}
