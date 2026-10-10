package netupload

import (
	"testing"
	"time"
)

func TestDefaultsWorkWithoutTheSection(t *testing.T) {
	cfg := ConfigFromDocument(map[string]any{})

	if !cfg.Enabled || cfg.Window != 60*time.Second || cfg.TolerancePercent != 20 || cfg.MinFileBytes != 1024 {
		t.Fatalf("умолчания: %+v", cfg)
	}
	if cfg.MaxFileBytes != 50*1024*1024 {
		t.Fatalf("MaxFileBytes = %d, берётся из collectors.artifact", cfg.MaxFileBytes)
	}
	if len(cfg.Extensions) == 0 || len(cfg.ExcludePaths) == 0 || len(cfg.Services) == 0 {
		t.Fatalf("встроенные списки пусты: %+v", cfg)
	}
}

func TestOverridesAreApplied(t *testing.T) {
	doc := map[string]any{"collectors": map[string]any{
		"artifact": map[string]any{"max_bytes": float64(1000)},
		"net_upload": map[string]any{
			"enabled":                false,
			"window_seconds":         float64(30),
			"size_tolerance_percent": float64(10),
			"min_file_bytes":         float64(500),
			"extensions":             []any{".PDF", "docx"},
			"exclude_paths":          []any{`*\Temp\*`},
			"services": []any{map[string]any{
				"key": "corp", "name": "Корп", "domains": []any{"cloud.example.kz"}, "cidrs": []any{"10.0.0.0/8"},
			}},
		},
	}}

	cfg := ConfigFromDocument(doc)

	if cfg.Enabled || cfg.Window != 30*time.Second || cfg.TolerancePercent != 10 || cfg.MinFileBytes != 500 {
		t.Fatalf("числа: %+v", cfg)
	}
	if cfg.MaxFileBytes != 1000 {
		t.Fatalf("MaxFileBytes = %d", cfg.MaxFileBytes)
	}
	if len(cfg.Extensions) != 2 || cfg.Extensions[0] != "pdf" || cfg.Extensions[1] != "docx" {
		t.Fatalf("расширения нормализуются: %v", cfg.Extensions)
	}
	if len(cfg.ExcludePaths) != 1 {
		t.Fatalf("исключения: %v", cfg.ExcludePaths)
	}
	if len(cfg.Services) != 1 || cfg.Services[0].Key != "corp" || cfg.Services[0].Domains[0] != "cloud.example.kz" {
		t.Fatalf("сервисы: %+v", cfg.Services)
	}
}

func TestNonsenseFallsBackToDefaults(t *testing.T) {
	doc := map[string]any{"collectors": map[string]any{"net_upload": map[string]any{
		"window_seconds":         float64(0),
		"size_tolerance_percent": float64(95),
		"min_file_bytes":         "много",
		"extensions":             []any{},
		"services":               []any{map[string]any{"name": "без ключа"}, "мусор"},
	}}}

	cfg := ConfigFromDocument(doc)

	def := DefaultConfig()
	if cfg.Window != def.Window || cfg.TolerancePercent != def.TolerancePercent || cfg.MinFileBytes != def.MinFileBytes {
		t.Fatalf("бессмысленное должно заменяться умолчанием: %+v", cfg)
	}
	if len(cfg.Services) != len(def.Services) {
		t.Fatalf("сервис без ключа не годится, берётся встроенный каталог: %d", len(cfg.Services))
	}
}
