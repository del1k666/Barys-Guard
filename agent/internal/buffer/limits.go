package buffer

import "time"

// Limits — пределы буфера из раздела 10 основной спеки.
type Limits struct {
	MaxBytes int64
	MaxAge   time.Duration
}

func DefaultLimits() Limits {
	return Limits{MaxBytes: 500 * 1024 * 1024, MaxAge: 7 * 24 * time.Hour}
}

// LimitsFromDocument читает пределы из документа конфигурации агента
// (раздел buffer). Отсутствующее или бессмысленное значение заменяется
// умолчанием: плохой документ не должен оставлять агента без буфера.
func LimitsFromDocument(document map[string]any) Limits {
	limits := DefaultLimits()
	section, _ := document["buffer"].(map[string]any)

	if value, ok := section["max_bytes"].(float64); ok && value > 0 {
		limits.MaxBytes = int64(value)
	}
	if value, ok := section["max_age_days"].(float64); ok && value > 0 {
		limits.MaxAge = time.Duration(value * float64(24*time.Hour))
	}
	return limits
}
