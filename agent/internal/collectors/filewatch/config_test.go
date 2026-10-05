package filewatch

import (
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"
)

func TestDefaultsWhenDocumentHasNoCollectors(t *testing.T) {
	cfg := ConfigFromDocument(nil, []string{`C:\Users\ivanov`}, `C:\ProgramData\BarysGuard`)

	if !cfg.Enabled || cfg.Stable != 1500*time.Millisecond || cfg.MaxWait != 30*time.Second {
		t.Fatalf("умолчания: %+v", cfg)
	}
	if cfg.MaxHashBytes != 256*1024*1024 || cfg.MaxEventsPerSecond != 200 {
		t.Fatalf("умолчания: %+v", cfg)
	}
	want := []string{`C:\Users\ivanov\Documents`, `C:\Users\ivanov\Desktop`, `C:\Users\ivanov\Downloads`}
	if !reflect.DeepEqual(cfg.Paths, want) {
		t.Fatalf("пути: %v", cfg.Paths)
	}
}

func TestUsersTokenExpandsForEveryProfile(t *testing.T) {
	doc := map[string]any{"collectors": map[string]any{"file_watch": map[string]any{
		"paths": []any{`%USERS%\Documents`, `D:\Shared`},
	}}}

	cfg := ConfigFromDocument(doc, []string{`C:\Users\a`, `C:\Users\b`}, "")

	want := []string{`C:\Users\a\Documents`, `C:\Users\b\Documents`, `D:\Shared`}
	if !reflect.DeepEqual(cfg.Paths, want) {
		t.Fatalf("пути: %v", cfg.Paths)
	}
}

func TestDocumentValuesOverrideDefaultsAndGarbageFallsBack(t *testing.T) {
	doc := map[string]any{"collectors": map[string]any{"file_watch": map[string]any{
		"enabled": false, "stable_ms": float64(500), "max_hash_bytes": "много", "max_events_per_second": float64(-5),
	}}}

	cfg := ConfigFromDocument(doc, nil, "")

	if cfg.Enabled || cfg.Stable != 500*time.Millisecond {
		t.Fatalf("значения документа: %+v", cfg)
	}
	if cfg.MaxHashBytes != 256*1024*1024 || cfg.MaxEventsPerSecond != 200 {
		t.Fatalf("мусор должен давать умолчание: %+v", cfg)
	}
}

func TestDataDirIsAlwaysExcluded(t *testing.T) {
	cfg := ConfigFromDocument(nil, nil, `C:\ProgramData\BarysGuard`)

	found := false
	for _, pattern := range cfg.Exclude {
		if pattern == `C:\ProgramData\BarysGuard\*` {
			found = true
		}
	}
	if !found {
		t.Fatalf("каталог данных агента не исключён: %v", cfg.Exclude)
	}
}

func TestProfileDirsSkipsSystemProfiles(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"ivanov", "petrov", "Public", "Default", "Default User", "All Users"} {
		os.Mkdir(filepath.Join(root, name), 0o755)
	}
	os.WriteFile(filepath.Join(root, "desktop.ini"), nil, 0o644)

	got := ProfileDirs(root)

	sort.Strings(got)
	want := []string{filepath.Join(root, "ivanov"), filepath.Join(root, "petrov")}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("профили: %v", got)
	}
}
