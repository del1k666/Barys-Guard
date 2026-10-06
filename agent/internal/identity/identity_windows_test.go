//go:build windows

package identity

import (
	"os"
	"path/filepath"
	"testing"
)

func TestFileOwnerOfAFileWeCreated(t *testing.T) {
	path := filepath.Join(t.TempDir(), "owned.txt")
	if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}

	owner := New().FileOwner(path)

	if owner == nil {
		t.Fatal("владелец не определён")
	}
	sid, _ := owner["user_sid"].(string)
	name, _ := owner["user_name"].(string)
	if len(sid) < 8 || sid[:4] != "S-1-" || name == "" {
		t.Fatalf("владелец: %+v", owner)
	}
}

func TestFileOwnerOfMissingFileIsNil(t *testing.T) {
	if New().FileOwner(filepath.Join(t.TempDir(), "нет-такого.txt")) != nil {
		t.Fatal("для несуществующего файла вернулся владелец")
	}
}

func TestConsoleUserDoesNotPanic(t *testing.T) {
	// На машине без интерактивной сессии (сервер сборки) пользователя нет, и это не ошибка.
	if user := New().ConsoleUser(); user != nil {
		if user["user_name"] == "" {
			t.Fatalf("пустое имя: %+v", user)
		}
		t.Logf("пользователь консоли: %+v", user)
	}
}
