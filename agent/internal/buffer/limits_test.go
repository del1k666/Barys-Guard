package buffer_test

import (
	"testing"
	"time"

	"github.com/barysguard/agent/internal/buffer"
)

func TestLimitsDefaultToSpecValues(t *testing.T) {
	limits := buffer.LimitsFromDocument(nil)

	if limits.MaxBytes != 500*1024*1024 || limits.MaxAge != 7*24*time.Hour {
		t.Fatalf("по умолчанию: %+v", limits)
	}
}

func TestLimitsAreReadFromConfigDocument(t *testing.T) {
	document := map[string]any{"buffer": map[string]any{"max_bytes": float64(2_000_000), "max_age_days": float64(2)}}

	limits := buffer.LimitsFromDocument(document)

	if limits.MaxBytes != 2_000_000 || limits.MaxAge != 48*time.Hour {
		t.Fatalf("из документа: %+v", limits)
	}
}

func TestNonsenseInDocumentFallsBackToDefaults(t *testing.T) {
	document := map[string]any{"buffer": map[string]any{"max_bytes": "много", "max_age_days": float64(-1)}}

	limits := buffer.LimitsFromDocument(document)

	if limits != buffer.DefaultLimits() {
		t.Fatalf("мусор в документе: %+v", limits)
	}
}
