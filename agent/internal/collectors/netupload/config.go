// Package netupload — сборщик «отправка файла в сеть»: находит документ,
// который процесс прочитал и отправил на адрес известного сервиса, снимает
// зашифрованную копию и присылает событие network/upload.
package netupload

import (
	"strings"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
)

// Умолчания совпадают с серверными (services/config.py): агент обязан работать
// и с документом, в котором раздела collectors.net_upload нет.
const (
	defaultWindowSeconds = 60
	defaultTolerance     = 20
	defaultMinFileBytes  = 1024
	maxTolerance         = 90
)

var (
	defaultExtensions = []string{
		"pdf", "docx", "xlsx", "pptx", "doc", "xls", "ppt", "rtf", "txt", "csv", "zip", "7z", "rar",
	}
	// Системные каталоги, профили приложений и корзина: чтение оттуда — не работа пользователя с документом.
	defaultExclude = []string{
		`*\AppData\*`, `?:\Windows\*`, `?:\Program Files*`, `?:\ProgramData\*`, `*\$Recycle.Bin\*`,
	}
)

// Service — внешний сервис, куда уходят файлы. Опознаётся по домену (по ответам
// DNS) или, если домен неизвестен, по диапазону адресов.
type Service struct {
	Key, Name string
	Domains   []string
	CIDRs     []string
}

type Config struct {
	Enabled bool
	// Window — окно «прочитал → отправил».
	Window time.Duration
	// TolerancePercent — допуск на сжатие и накладные расходы.
	TolerancePercent int
	MinFileBytes     int64
	// MaxFileBytes — предел размера файла; берётся из collectors.artifact.max_bytes.
	MaxFileBytes int64
	Extensions   []string
	ExcludePaths []string
	Services     []Service
}

func DefaultConfig() Config {
	return Config{
		Enabled:          true,
		Window:           defaultWindowSeconds * time.Second,
		TolerancePercent: defaultTolerance,
		MinFileBytes:     defaultMinFileBytes,
		MaxFileBytes:     artifacts.DefaultConfig().MaxBytes,
		Extensions:       append([]string(nil), defaultExtensions...),
		ExcludePaths:     append([]string(nil), defaultExclude...),
		Services:         DefaultServices(),
	}
}

// ConfigFromDocument читает collectors.net_upload. Отсутствующее или
// бессмысленное значение заменяется умолчанием; пустой список означает
// «встроенное значение».
func ConfigFromDocument(document map[string]any) Config {
	cfg := DefaultConfig()
	cfg.MaxFileBytes = artifacts.ConfigFromDocument(document).MaxBytes

	collectors, _ := document["collectors"].(map[string]any)
	section, _ := collectors["net_upload"].(map[string]any)

	if value, ok := section["enabled"].(bool); ok {
		cfg.Enabled = value
	}
	if value, ok := section["window_seconds"].(float64); ok && value >= 1 {
		cfg.Window = time.Duration(value) * time.Second
	}
	if value, ok := section["size_tolerance_percent"].(float64); ok && value >= 0 && value <= maxTolerance {
		cfg.TolerancePercent = int(value)
	}
	if value, ok := section["min_file_bytes"].(float64); ok && value >= 1 {
		cfg.MinFileBytes = int64(value)
	}
	if list := stringList(section["extensions"]); len(list) > 0 {
		cfg.Extensions = normalizeExtensions(list)
	}
	if list := stringList(section["exclude_paths"]); len(list) > 0 {
		cfg.ExcludePaths = list
	}
	if services := servicesFrom(section["services"]); len(services) > 0 {
		cfg.Services = services
	}
	return cfg
}

func stringList(raw any) []string {
	items, _ := raw.([]any)
	var out []string
	for _, item := range items {
		if text, ok := item.(string); ok && strings.TrimSpace(text) != "" {
			out = append(out, strings.TrimSpace(text))
		}
	}
	return out
}

func normalizeExtensions(list []string) []string {
	out := make([]string, 0, len(list))
	for _, ext := range list {
		out = append(out, strings.ToLower(strings.TrimPrefix(ext, ".")))
	}
	return out
}

func servicesFrom(raw any) []Service {
	items, _ := raw.([]any)
	var out []Service
	for _, item := range items {
		entry, _ := item.(map[string]any)
		key, _ := entry["key"].(string)
		if strings.TrimSpace(key) == "" {
			continue
		}
		name, _ := entry["name"].(string)
		if name == "" {
			name = key
		}
		out = append(out, Service{
			Key: key, Name: name,
			Domains: stringList(entry["domains"]), CIDRs: stringList(entry["cidrs"]),
		})
	}
	return out
}
