package filewatch

import (
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
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

// OneDrive Known Folder Move переносит «Документы» и «Рабочий стол» в каталог
// «OneDrive - Организация»: старые папки остаются пустыми, а настоящие надо найти.
func TestUsersPathsWithAWildcardExpandToExistingFoldersOnly(t *testing.T) {
	root := t.TempDir()
	profile := filepath.Join(root, "ivanov")
	real := filepath.Join(profile, "OneDrive - Контора", "Documents")
	os.MkdirAll(real, 0o755)
	os.MkdirAll(filepath.Join(profile, "OneDrive - Контора", "Pictures"), 0o755)
	doc := map[string]any{"collectors": map[string]any{"file_watch": map[string]any{
		"paths": []any{filepath.Join("%USERS%", "OneDrive*", "Documents"), filepath.Join("%USERS%", "OneDrive*", "Desktop")},
	}}}

	cfg := ConfigFromDocument(doc, []string{profile}, "")

	if !reflect.DeepEqual(cfg.Paths, []string{real}) {
		t.Fatalf("пути: %v", cfg.Paths)
	}
}

func TestDefaultPathsIncludeOneDriveKnownFolders(t *testing.T) {
	joined := strings.Join(defaultPaths, "\n")
	for _, folder := range []string{"Documents", "Desktop", "Downloads"} {
		if !strings.Contains(joined, `%USERS%\OneDrive*\`+folder) {
			t.Errorf("в умолчаниях нет OneDrive\\%s", folder)
		}
	}
}
