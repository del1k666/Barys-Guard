package netupload

import (
	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

// BuildEvent собирает конверт network/upload. Процесс и актёра добавляет сборщик.
func BuildEvent(match Match, hash filewatch.HashResult, vol volumes.Volume) (events.Envelope, error) {
	subject := map[string]any{
		"src_path": match.Read.Path,
		"volume": map[string]any{
			"type": vol.Type, "serial": vol.Serial, "label": vol.Label, "fs": vol.FS,
		},
		"size_bytes":   match.Read.Size,
		"service":      match.Service.Key,
		"service_name": match.Service.Name,
		"sent_bytes":   match.Sent,
		"confidence":   match.Confidence,
	}
	if match.Host != "" {
		subject["dest_host"] = match.Host
	}
	labels := map[string]any{}

	env, err := events.NewEnvelope(events.ChannelNetwork, "upload", events.SeverityHigh, subject)
	if err != nil {
		return events.Envelope{}, err
	}

	switch hash.Status {
	case filewatch.HashOK:
		env.Artifact = &events.Artifact{SHA256: hash.SHA256, Size: hash.Size, Uploaded: false}
		subject["size_bytes"] = hash.Size
	case filewatch.HashSkippedSize:
		labels["hash"] = "skipped_size"
		subject["size_bytes"] = hash.Size
	case filewatch.HashUnavailable:
		labels["hash"] = "unavailable"
	}
	if hash.StageSkip == artifacts.SkipRate {
		// Бюджет копирования исчерпан: оператор видит, что содержимое не взято.
		labels["artifact_skipped"] = "rate"
	}
	env.Labels = labels
	return env, nil
}
