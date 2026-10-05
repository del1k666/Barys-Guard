//go:build windows

package filewatch

import (
	"context"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func startRoot(t *testing.T, root string) (<-chan Raw, context.CancelFunc, <-chan error) {
	t.Helper()
	out := make(chan Raw, 256)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- DefaultStartWatcher(ctx, root, out, func(string) {}) }()
	time.Sleep(200 * time.Millisecond) // дать ReadDirectoryChanges встать на ожидание
	return out, cancel, done
}

func waitRaw(t *testing.T, out <-chan Raw, match func(Raw) bool) Raw {
	t.Helper()
	timeout := time.After(5 * time.Second)
	for {
		select {
		case raw := <-out:
			if match(raw) {
				return raw
			}
		case <-timeout:
			t.Fatal("ожидаемое уведомление не пришло")
		}
	}
}

func TestWatcherSeesCreateModifyRenameAndDelete(t *testing.T) {
	root := t.TempDir()
	out, cancel, done := startRoot(t, root)
	defer func() { cancel(); <-done }()

	name := filepath.Join(root, "Отчёт 2026.txt")
	if err := os.WriteFile(name, []byte("первая версия"), 0o600); err != nil {
		t.Fatal(err)
	}
	waitRaw(t, out, func(r Raw) bool { return r.Kind == Created && r.Path == name })

	renamed := filepath.Join(root, "Отчёт — финал.txt")
	if err := os.Rename(name, renamed); err != nil {
		t.Fatal(err)
	}
	got := waitRaw(t, out, func(r Raw) bool { return r.Kind == Renamed })
	if got.OldPath != name || got.Path != renamed {
		t.Fatalf("переименование: %+v", got)
	}

	if err := os.Remove(renamed); err != nil {
		t.Fatal(err)
	}
	waitRaw(t, out, func(r Raw) bool { return r.Kind == Deleted && r.Path == renamed })
}

func TestWatcherIsRecursive(t *testing.T) {
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, "вложенная", "глубже"), 0o755)
	out, cancel, done := startRoot(t, root)
	defer func() { cancel(); <-done }()

	deep := filepath.Join(root, "вложенная", "глубже", "f.txt")
	os.WriteFile(deep, []byte("x"), 0o600)

	waitRaw(t, out, func(r Raw) bool { return r.Kind == Created && r.Path == deep })
}

func TestWatcherStopsPromptlyOnCancel(t *testing.T) {
	_, cancel, done := startRoot(t, t.TempDir())

	cancel()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("остановка вернула ошибку: %v", err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("наблюдатель завис после отмены")
	}
}

func TestMissingRootIsReportedAsNotExist(t *testing.T) {
	err := DefaultStartWatcher(context.Background(), filepath.Join(t.TempDir(), "нет"), make(chan Raw, 1), func(string) {})

	if !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("ошибка: %v", err)
	}
}
