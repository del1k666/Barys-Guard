package artifacts

import "testing"

func TestDefaultsMatchTheSpec(t *testing.T) {
	got := ConfigFromDocument(nil)
	want := Config{
		Enabled: true, MaxBytes: 52_428_800, StagingMaxBytes: 524_288_000,
		UploadBytesPerSecond: 2_097_152, StageBytesPerMinute: 209_715_200,
	}
	if got != want {
		t.Fatalf("умолчания: %+v, ожидалось %+v", got, want)
	}
}

func TestDocumentOverridesDefaults(t *testing.T) {
	document := map[string]any{"collectors": map[string]any{"artifact": map[string]any{
		"enabled": false, "max_bytes": float64(1000), "staging_max_bytes": float64(2000),
		"upload_bytes_per_second": float64(3000), "stage_bytes_per_minute": float64(4000),
	}}}
	got := ConfigFromDocument(document)
	want := Config{Enabled: false, MaxBytes: 1000, StagingMaxBytes: 2000, UploadBytesPerSecond: 3000, StageBytesPerMinute: 4000}
	if got != want {
		t.Fatalf("%+v, ожидалось %+v", got, want)
	}
}

// Плохой документ не должен оставлять агента без загрузки или с нулевым лимитом.
func TestGarbageValuesFallBackToDefaults(t *testing.T) {
	document := map[string]any{"collectors": map[string]any{"artifact": map[string]any{
		"enabled": "yes", "max_bytes": float64(-5), "staging_max_bytes": "много",
		"upload_bytes_per_second": float64(0), "stage_bytes_per_minute": nil,
	}}}
	if got := ConfigFromDocument(document); got != DefaultConfig() {
		t.Fatalf("%+v, ожидались умолчания", got)
	}
}
