package events

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestNewEnvelopeFillsIdentityAndDefaults(t *testing.T) {
	env, err := NewEnvelope(ChannelAgent, "start", SeverityInfo, map[string]any{"component": "agent"})
	if err != nil {
		t.Fatalf("NewEnvelope: %v", err)
	}

	if env.SchemaVersion != SchemaVersion {
		t.Errorf("schema_version = %d", env.SchemaVersion)
	}
	if len(env.EventID) != 36 || env.OccurredAt.IsZero() {
		t.Errorf("не заполнены event_id/occurred_at: %+v", env)
	}
	if env.Channel != "agent" || env.Action != "start" || env.SeverityHint != "info" {
		t.Errorf("поля: %+v", env)
	}
}

func TestMarshalLineIsSingleLineWithObjectsInsteadOfNull(t *testing.T) {
	env, _ := NewEnvelope(ChannelAgent, "start", SeverityInfo, nil)
	env.Subject = map[string]any{"detail": "строка\nс переводом"}

	line, err := env.MarshalLine()
	if err != nil {
		t.Fatalf("MarshalLine: %v", err)
	}

	if bytes.ContainsAny(line, "\n\r") {
		t.Fatalf("в строке есть перевод строки: %q", line)
	}
	var decoded map[string]any
	if err := json.Unmarshal(line, &decoded); err != nil {
		t.Fatalf("не JSON: %v", err)
	}
	// Сервер отвергает null там, где ждёт объект.
	for _, key := range []string{"actor", "process", "subject", "labels"} {
		if _, ok := decoded[key].(map[string]any); !ok {
			t.Errorf("%s = %v, ожидался объект", key, decoded[key])
		}
	}
}

func TestSeverityRankOrdersLevels(t *testing.T) {
	order := []string{SeverityInfo, SeverityLow, SeverityMedium, SeverityHigh, SeverityCritical}
	for i, severity := range order {
		if got := SeverityRank(severity); int(got) != i {
			t.Errorf("SeverityRank(%q) = %d, ожидалось %d", severity, got, i)
		}
	}
	if SeverityRank("что-то") != 0 {
		t.Error("неизвестная критичность должна считаться info")
	}
	if SeverityRank(SeverityCritical) != CriticalRank {
		t.Error("CriticalRank расходится с SeverityRank")
	}
}
