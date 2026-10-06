// Package artifacts — копии файлов с внешних томов и их загрузка на сервер.
//
// Копия снимается в том же проходе, что и хеширование, хранится зашифрованной
// и уходит на сервер отдельным воркером с низким приоритетом.
package artifacts

// Умолчания совпадают с серверными (services/config.py): агент обязан работать
// и с документом, в котором раздела collectors.artifact нет.
const (
	defaultMaxBytes       = 50 * 1024 * 1024
	defaultStagingMax     = 500 * 1024 * 1024
	defaultUploadPerSec   = 2 * 1024 * 1024
	defaultStagePerMinute = 200 * 1024 * 1024
)

type Config struct {
	Enabled bool
	// MaxBytes — предельный размер файла для загрузки.
	MaxBytes int64
	// StagingMaxBytes — предел каталога копий; сверх него вытесняются старые.
	StagingMaxBytes int64
	// UploadBytesPerSecond ограничивает скорость отправки.
	UploadBytesPerSecond int64
	// StageBytesPerMinute — бюджет копирования: сверх него файл пропускается.
	StageBytesPerMinute int64
}

func DefaultConfig() Config {
	return Config{
		Enabled:              true,
		MaxBytes:             defaultMaxBytes,
		StagingMaxBytes:      defaultStagingMax,
		UploadBytesPerSecond: defaultUploadPerSec,
		StageBytesPerMinute:  defaultStagePerMinute,
	}
}

// ConfigFromDocument читает collectors.artifact. Отсутствующее или
// бессмысленное значение заменяется умолчанием.
func ConfigFromDocument(document map[string]any) Config {
	cfg := DefaultConfig()
	collectors, _ := document["collectors"].(map[string]any)
	section, _ := collectors["artifact"].(map[string]any)

	if enabled, ok := section["enabled"].(bool); ok {
		cfg.Enabled = enabled
	}
	cfg.MaxBytes = positive(section, "max_bytes", cfg.MaxBytes)
	cfg.StagingMaxBytes = positive(section, "staging_max_bytes", cfg.StagingMaxBytes)
	cfg.UploadBytesPerSecond = positive(section, "upload_bytes_per_second", cfg.UploadBytesPerSecond)
	cfg.StageBytesPerMinute = positive(section, "stage_bytes_per_minute", cfg.StageBytesPerMinute)
	return cfg
}

func positive(section map[string]any, key string, fallback int64) int64 {
	if value, ok := section[key].(float64); ok && value >= 1 {
		return int64(value)
	}
	return fallback
}
