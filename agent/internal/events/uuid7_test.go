package events

import (
	"fmt"
	"strings"
	"testing"
	"time"
)

func formatMillis(at time.Time) string {
	return strings.ToLower(fmt.Sprintf("%012x", uint64(at.UnixMilli())))
}

func TestUUIDv7HasVersionVariantAndTimestamp(t *testing.T) {
	at := time.Date(2026, 10, 5, 10, 0, 0, 0, time.UTC)

	id, err := NewUUIDv7(at)
	if err != nil {
		t.Fatalf("NewUUIDv7: %v", err)
	}

	parts := strings.Split(id, "-")
	if len(parts) != 5 || len(id) != 36 {
		t.Fatalf("не UUID: %q", id)
	}
	if parts[2][0] != '7' {
		t.Errorf("версия %q, ожидалась 7", parts[2][:1])
	}
	if !strings.ContainsRune("89ab", rune(parts[3][0])) {
		t.Errorf("вариант %q вне 8..b", parts[3][:1])
	}
	// Первые 48 бит — миллисекунды с эпохи.
	if got := parts[0] + parts[1]; got != formatMillis(at) {
		t.Errorf("метка времени %s, ожидалась %s", got, formatMillis(at))
	}
}

func TestUUIDv7SortsByTime(t *testing.T) {
	earlier, _ := NewUUIDv7(time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))
	later, _ := NewUUIDv7(time.Date(2026, 1, 1, 0, 0, 1, 0, time.UTC))

	if !(earlier < later) {
		t.Fatalf("%s не меньше %s", earlier, later)
	}
}

func TestUUIDv7IsUnique(t *testing.T) {
	at := time.Now()
	seen := map[string]bool{}
	for i := 0; i < 1000; i++ {
		id, _ := NewUUIDv7(at)
		if seen[id] {
			t.Fatalf("повтор %s", id)
		}
		seen[id] = true
	}
}
