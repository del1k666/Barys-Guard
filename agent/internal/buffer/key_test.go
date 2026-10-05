package buffer_test

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/barysguard/agent/internal/buffer"
	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/platform"
)

func TestKeyIsCreatedOnceAndReused(t *testing.T) {
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()

	first, created, err := buffer.LoadOrCreateKey(layout, guard)
	if err != nil || !created || len(first) != 32 {
		t.Fatalf("первый вызов: key=%d created=%v err=%v", len(first), created, err)
	}

	second, created, err := buffer.LoadOrCreateKey(layout, guard)
	if err != nil || created || !bytes.Equal(first, second) {
		t.Fatalf("второй вызов: created=%v err=%v равны=%v", created, err, bytes.Equal(first, second))
	}
}

func TestCorruptKeyFileIsTreatedAsLostKey(t *testing.T) {
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()
	if _, _, err := buffer.LoadOrCreateKey(layout, guard); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(layout.BufferKeyPath(), []byte("не ключ"), 0o600); err != nil {
		t.Fatal(err)
	}

	key, created, err := buffer.LoadOrCreateKey(layout, guard)

	if err != nil || !created || len(key) != 32 {
		t.Fatalf("created=%v len=%d err=%v", created, len(key), err)
	}
}

func TestOpenAtResetsBufferWhenKeyIsLost(t *testing.T) {
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()

	first, reset, err := buffer.OpenAt(layout, guard, buffer.DefaultLimits())
	if err != nil || reset {
		t.Fatalf("первый запуск: reset=%v err=%v", reset, err)
	}
	env, _ := events.NewEnvelope(events.ChannelAgent, "start", events.SeverityInfo, nil)
	if err := first.Append(env); err != nil {
		t.Fatal(err)
	}
	first.Close()

	if err := os.Remove(layout.BufferKeyPath()); err != nil {
		t.Fatal(err)
	}

	second, reset, err := buffer.OpenAt(layout, guard, buffer.DefaultLimits())
	if err != nil {
		t.Fatalf("OpenAt после потери ключа: %v", err)
	}
	defer second.Close()

	if !reset {
		t.Fatal("потеря ключа не сообщена")
	}
	if count, _ := second.Stats(); count != 0 {
		t.Fatalf("буфер не пуст: %d", count)
	}
}

// Повреждённый файл буфера (обрыв питания, сбой диска) не должен мешать
// агенту стартовать: иначе вместе с буфером встают heartbeat, команды и
// продление сертификата.
func TestOpenAtRecoversFromCorruptDatabase(t *testing.T) {
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()
	first, _, err := buffer.OpenAt(layout, guard, buffer.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	first.Close()
	if err := os.WriteFile(layout.BufferPath(), bytes.Repeat([]byte("мусор"), 2000), 0o600); err != nil {
		t.Fatal(err)
	}

	second, reset, err := buffer.OpenAt(layout, guard, buffer.DefaultLimits())
	if err != nil {
		t.Fatalf("OpenAt с повреждённым файлом: %v", err)
	}
	defer second.Close()

	if !reset {
		t.Fatal("пересоздание буфера не сообщено")
	}
	matches, _ := filepath.Glob(layout.BufferPath() + ".corrupt-*")
	if len(matches) != 1 {
		t.Fatalf("повреждённый файл не сохранён рядом для разбора: %v", matches)
	}
	env, _ := events.NewEnvelope(events.ChannelAgent, "start", events.SeverityInfo, nil)
	if err := second.Append(env); err != nil {
		t.Fatalf("новый буфер не работает: %v", err)
	}
}
