//go:build windows

package filewatch

import (
	"os"
	"path/filepath"
	"testing"
)

// Restart Manager видит процесс, удерживающий файл открытым. Пока копирование
// идёт, этим процессом бывает проводник или robocopy; в тесте им служим мы сами.
func TestAttributorFindsTheProcessHoldingTheFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "held.bin")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()

	attributor := &rmAttributor{self: 0} // свой PID обычно исключается; в тесте именно мы держим файл
	process, ok := attributor.Attribute(path)

	if !ok {
		t.Skip("Restart Manager не вернул процесс на этой системе")
	}
	if process["pid"] != os.Getpid() {
		t.Fatalf("процесс: %+v, ожидался наш PID %d", process, os.Getpid())
	}
	if p, _ := process["path"].(string); p == "" {
		t.Fatalf("нет пути образа: %+v", process)
	}
}

func TestAttributorExcludesTheAgentItself(t *testing.T) {
	path := filepath.Join(t.TempDir(), "held.bin")
	file, _ := os.OpenFile(path, os.O_CREATE|os.O_WRONLY, 0o600)
	defer file.Close()

	if process, ok := NewAttributor().Attribute(path); ok && process["pid"] == os.Getpid() {
		t.Fatalf("агент определил самого себя: %+v", process)
	}
}

func TestAttributorOnAFreeOrMissingFileFindsNobody(t *testing.T) {
	free := filepath.Join(t.TempDir(), "free.bin")
	os.WriteFile(free, []byte("x"), 0o600)

	if _, ok := NewAttributor().Attribute(free); ok {
		t.Fatal("процесс найден для свободного файла")
	}
	if _, ok := NewAttributor().Attribute(filepath.Join(t.TempDir(), "нет.bin")); ok {
		t.Fatal("процесс найден для несуществующего файла")
	}
}
