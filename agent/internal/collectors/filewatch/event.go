package filewatch

import (
	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

type EventInput struct {
	Action                    Action
	DstPath, SrcPath, OldPath string
	Volume                    volumes.Volume
	Hash                      HashResult
	Actor, Process            map[string]any
}

// severityFor: подсказка агента. Копирование известного файла на внешний том —
// главный сценарий утечки; запись неизвестного — повод присмотреться.
func severityFor(action Action, vol volumes.Volume) string {
	if vol.Type != volumes.TypeRemovable {
		return events.SeverityInfo
	}
	switch action {
	case ActionCopy:
		return events.SeverityHigh
	case ActionCreate, ActionModify:
		return events.SeverityMedium
	}
	return events.SeverityInfo
}

// BuildEvent собирает конверт канала file (раздел 7.1 спеки).
func BuildEvent(in EventInput) (events.Envelope, error) {
	subject := map[string]any{
		"dst_path": in.DstPath,
		"volume": map[string]any{
			"type": in.Volume.Type, "serial": in.Volume.Serial, "label": in.Volume.Label, "fs": in.Volume.FS,
		},
	}
	if in.Action == ActionCopy && in.SrcPath != "" {
		subject["src_path"] = in.SrcPath
	}
	if in.Action == ActionRename && in.OldPath != "" {
		subject["old_path"] = in.OldPath
	}

	env, err := events.NewEnvelope(events.ChannelFile, string(in.Action), severityFor(in.Action, in.Volume), subject)
	if err != nil {
		return events.Envelope{}, err
	}

	labels := map[string]any{}
	switch in.Hash.Status {
	case HashOK:
		env.Artifact = &events.Artifact{SHA256: in.Hash.SHA256, Size: in.Hash.Size, Uploaded: false}
		subject["size_bytes"] = in.Hash.Size
	case HashSkippedSize:
		labels["hash"] = "skipped_size"
		subject["size_bytes"] = in.Hash.Size
	case HashUnavailable:
		labels["hash"] = "unavailable"
	}
	if in.Hash.StageSkip == artifacts.SkipRate {
		// Бюджет копирования исчерпан: оператор видит, что содержимое не взято.
		labels["artifact_skipped"] = "rate"
	}
	if in.Volume.Type == volumes.TypeRemovable && in.Action != ActionDelete && in.Process == nil {
		labels["process"] = "unknown"
	}

	env.Actor = in.Actor
	env.Process = in.Process
	env.Labels = labels
	return env, nil
}
