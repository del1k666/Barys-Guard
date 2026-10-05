// Package events описывает события агента: конверт, очередь и интерфейс
// сборщика. Остальные пакеты о форме события ничего не знают.
package events

import (
	"encoding/json"
	"time"
)

// SchemaVersion — версия конверта. Сервер принимает только известные версии.
const SchemaVersion = 1

// Каналы перехвата. Список совпадает с контрактом.
const (
	ChannelFile      = "file"
	ChannelUSB       = "usb"
	ChannelClipboard = "clipboard"
	ChannelNetwork   = "network"
	ChannelPrint     = "print"
	ChannelProcess   = "process"
	ChannelAgent     = "agent"
)

// Предварительная оценка критичности; окончательную определяет сервер.
const (
	SeverityInfo     = "info"
	SeverityLow      = "low"
	SeverityMedium   = "medium"
	SeverityHigh     = "high"
	SeverityCritical = "critical"
)

// CriticalRank — ранг, который буфер никогда не вытесняет.
const CriticalRank uint8 = 4

// SeverityRank переводит критичность в число для политики вытеснения.
// Неизвестное значение считается наименее важным: лучше потерять событие
// с опечаткой в критичности, чем вытеснить из-за него настоящее.
func SeverityRank(severity string) uint8 {
	switch severity {
	case SeverityLow:
		return 1
	case SeverityMedium:
		return 2
	case SeverityHigh:
		return 3
	case SeverityCritical:
		return CriticalRank
	}
	return 0
}

type Artifact struct {
	SHA256   string `json:"sha256"`
	Size     int64  `json:"size"`
	Uploaded bool   `json:"uploaded"`
}

// Envelope — конверт события, раздел 9 основной спеки.
// agent_id в нём нет намеренно: личность агента определяет сертификат.
type Envelope struct {
	EventID       string         `json:"event_id"`
	SchemaVersion int            `json:"schema_version"`
	OccurredAt    time.Time      `json:"occurred_at"`
	Channel       string         `json:"channel"`
	Action        string         `json:"action"`
	SeverityHint  string         `json:"severity_hint"`
	Actor         map[string]any `json:"actor"`
	Process       map[string]any `json:"process"`
	Subject       map[string]any `json:"subject"`
	Labels        map[string]any `json:"labels"`
	Artifact      *Artifact      `json:"artifact,omitempty"`
}

// NewEnvelope заполняет идентификатор и время и отдаёт событие без актёра и процесса.
func NewEnvelope(channel, action, severity string, subject map[string]any) (Envelope, error) {
	now := time.Now().UTC()
	id, err := NewUUIDv7(now)
	if err != nil {
		return Envelope{}, err
	}
	return Envelope{
		EventID:       id,
		SchemaVersion: SchemaVersion,
		OccurredAt:    now,
		Channel:       channel,
		Action:        action,
		SeverityHint:  severity,
		Subject:       subject,
	}, nil
}

// MarshalLine кодирует событие одной строкой JSON без перевода строки:
// строка становится записью NDJSON как есть.
//
// Пустые карты заменяются на {}. Сервер отвергает null там, где ждёт объект,
// а карта, которую сборщик не заполнил, в Go по умолчанию равна nil.
func (e Envelope) MarshalLine() ([]byte, error) {
	for _, field := range []*map[string]any{&e.Actor, &e.Process, &e.Subject, &e.Labels} {
		if *field == nil {
			*field = map[string]any{}
		}
	}
	return json.Marshal(e)
}
