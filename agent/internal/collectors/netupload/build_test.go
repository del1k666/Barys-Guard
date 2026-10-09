package netupload

import (
	"testing"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

func sampleMatch() Match {
	return Match{
		PID:     42,
		Read:    Read{Path: `C:\Users\a\Documents\plan.pdf`, Size: 10_000, At: time.Unix(1, 0)},
		Service: Service{Key: "gdrive", Name: "Google Drive"},
		Host:    "drive.google.com", Sent: 10_800, Confidence: ConfidenceHigh,
	}
}

func TestBuildEventWithArtifact(t *testing.T) {
	hash := filewatch.HashResult{
		SHA256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
		Size:   10_000, Status: filewatch.HashOK, Staged: true,
	}

	env, err := BuildEvent(sampleMatch(), hash, volumes.Volume{Type: volumes.TypeFixed, FS: "NTFS"})
	if err != nil {
		t.Fatal(err)
	}

	if env.Channel != events.ChannelNetwork || env.Action != "upload" || env.SeverityHint != events.SeverityHigh {
		t.Fatalf("канал/действие/критичность: %s/%s/%s", env.Channel, env.Action, env.SeverityHint)
	}
	want := map[string]any{
		"src_path": `C:\Users\a\Documents\plan.pdf`, "size_bytes": int64(10_000), "service": "gdrive",
		"service_name": "Google Drive", "dest_host": "drive.google.com", "sent_bytes": uint64(10_800),
		"confidence": "high",
	}
	for key, value := range want {
		if env.Subject[key] != value {
			t.Errorf("subject[%q] = %#v, ждали %#v", key, env.Subject[key], value)
		}
	}
	volume, _ := env.Subject["volume"].(map[string]any)
	if volume["type"] != volumes.TypeFixed {
		t.Errorf("volume = %v", env.Subject["volume"])
	}
	if env.Artifact == nil || env.Artifact.SHA256 != hash.SHA256 || env.Artifact.Size != 10_000 || env.Artifact.Uploaded {
		t.Errorf("artifact = %+v", env.Artifact)
	}
}

func TestBuildEventWithoutCopyKeepsTheReason(t *testing.T) {
	cases := []struct {
		name  string
		hash  filewatch.HashResult
		label string
		value any
	}{
		{"слишком большой", filewatch.HashResult{Size: 99, Status: filewatch.HashSkippedSize}, "hash", "skipped_size"},
		{"недоступен", filewatch.HashResult{Status: filewatch.HashUnavailable}, "hash", "unavailable"},
		{"лимит скорости", filewatch.HashResult{SHA256: "a", Size: 1, Status: filewatch.HashOK, StageSkip: artifacts.SkipRate}, "artifact_skipped", "rate"},
	}
	for _, tc := range cases {
		env, err := BuildEvent(sampleMatch(), tc.hash, volumes.Volume{Type: volumes.TypeUnknown})
		if err != nil {
			t.Fatal(err)
		}
		if env.Labels[tc.label] != tc.value {
			t.Errorf("%s: labels = %v", tc.name, env.Labels)
		}
		if tc.hash.Status != filewatch.HashOK && env.Artifact != nil {
			t.Errorf("%s: без хеша не должно быть artifact", tc.name)
		}
	}
}

func TestBuildEventOmitsEmptyHost(t *testing.T) {
	match := sampleMatch()
	match.Host = ""

	env, err := BuildEvent(match, filewatch.HashResult{Status: filewatch.HashUnavailable}, volumes.Volume{Type: volumes.TypeUnknown})
	if err != nil {
		t.Fatal(err)
	}
	if _, present := env.Subject["dest_host"]; present {
		t.Fatalf("пустой домен не передаётся: %v", env.Subject)
	}
	if env.Subject["size_bytes"] != int64(10_000) {
		t.Fatalf("размер берётся из чтения, если хеша нет: %v", env.Subject["size_bytes"])
	}
}
