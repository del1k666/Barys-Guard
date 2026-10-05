//go:build windows

package filewatch

import (
	"os"
	"path/filepath"
	"testing"
)

// Пока агент считает хеш, пользователь должен иметь возможность удалить или
// переименовать файл: иначе проводник пишет «файл открыт в barysguard-agent»,
// а сохранение в Office падает.
func TestHashingDoesNotBlockDeleteOrRename(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "документ.docx")
	if err := os.WriteFile(path, []byte("данные"), 0o600); err != nil {
		t.Fatal(err)
	}

	reader, _, err := openFile(path)
	if err != nil {
		t.Fatalf("openFile: %v", err)
	}
	defer reader.Close()

	renamed := filepath.Join(dir, "переименован.docx")
	if err := os.Rename(path, renamed); err != nil {
		t.Fatalf("переименование при открытом на хеширование файле: %v", err)
	}
	if err := os.Remove(renamed); err != nil {
		t.Fatalf("удаление при открытом на хеширование файле: %v", err)
	}
}

func TestOpenFileStillRejectsDirectories(t *testing.T) {
	if _, _, err := openFile(t.TempDir()); err == nil {
		t.Fatal("каталог открылся как файл")
	}
}
