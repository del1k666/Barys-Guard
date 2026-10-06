package filewatch

import (
	"testing"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

var removable = volumes.Volume{DriveLetter: "E:", Type: volumes.TypeRemovable, Serial: "0781-5583", Label: "KINGSTON", FS: "NTFS"}
var fixed = volumes.Volume{DriveLetter: "C:", Type: volumes.TypeFixed, Serial: "AAAA-BBBB", FS: "NTFS"}

func TestCopyToRemovableIsHighWithSourceAndArtifact(t *testing.T) {
	env, err := BuildEvent(EventInput{
		Action: ActionCopy, DstPath: `E:\отчёт.xlsx`, SrcPath: `C:\Users\u\Documents\отчёт.xlsx`, Volume: removable,
		Hash:    HashResult{SHA256: "a3f9", Size: 184320, Status: HashOK},
		Actor:   map[string]any{"user_name": "PC\\u"},
		Process: map[string]any{"pid": 4812, "path": `C:\Windows\explorer.exe`},
	})
	if err != nil {
		t.Fatal(err)
	}

	if env.Channel != "file" || env.Action != "copy" || env.SeverityHint != events.SeverityHigh {
		t.Fatalf("конверт: %+v", env)
	}
	if env.Subject["dst_path"] != `E:\отчёт.xlsx` || env.Subject["src_path"] != `C:\Users\u\Documents\отчёт.xlsx` {
		t.Fatalf("пути: %+v", env.Subject)
	}
	volume := env.Subject["volume"].(map[string]any)
	if volume["type"] != "removable" || volume["serial"] != "0781-5583" {
		t.Fatalf("том: %+v", volume)
	}
	if env.Artifact == nil || env.Artifact.SHA256 != "a3f9" || env.Artifact.Size != 184320 || env.Artifact.Uploaded {
		t.Fatalf("артефакт: %+v", env.Artifact)
	}
	if env.Process["pid"] != 4812 || env.Actor["user_name"] != "PC\\u" {
		t.Fatalf("актёр и процесс: %+v %+v", env.Actor, env.Process)
	}
	if _, has := env.Labels["process"]; has {
		t.Fatalf("процесс известен, метка не нужна: %+v", env.Labels)
	}
}

func TestSeverityByLocationAndAction(t *testing.T) {
	cases := []struct {
		action Action
		vol    volumes.Volume
		want   string
	}{
		{ActionCopy, removable, events.SeverityHigh},
		{ActionDelete, removable, events.SeverityInfo},
		{ActionRename, removable, events.SeverityInfo},
		{ActionCreate, fixed, events.SeverityInfo},
		{ActionModify, fixed, events.SeverityInfo},
	}
	for _, c := range cases {
		env, err := BuildEvent(EventInput{Action: c.action, DstPath: `X:\f`, Volume: c.vol, OldPath: `X:\o`})
		if err != nil {
			t.Fatal(err)
		}
		if env.SeverityHint != c.want {
			t.Errorf("%s на %s: %s, ожидалось %s", c.action, c.vol.Type, env.SeverityHint, c.want)
		}
	}
}

func TestUnknownProcessOnRemovableIsLabelled(t *testing.T) {
	env, _ := BuildEvent(EventInput{Action: ActionCreate, DstPath: `E:\a`, Volume: removable, Hash: HashResult{SHA256: "aa", Size: 1, Status: HashOK}})

	if env.Labels["process"] != "unknown" {
		t.Fatalf("метки: %+v", env.Labels)
	}
	del, _ := BuildEvent(EventInput{Action: ActionDelete, DstPath: `E:\a`, Volume: removable})
	if _, has := del.Labels["process"]; has {
		t.Fatalf("для удаления процесс не определяется: %+v", del.Labels)
	}
}

func TestMissingHashIsExplainedInLabelsAndOmitsArtifact(t *testing.T) {
	skipped, _ := BuildEvent(EventInput{Action: ActionCreate, DstPath: `E:\big`, Volume: removable,
		Hash: HashResult{Size: 5 << 30, Status: HashSkippedSize}})
	if skipped.Artifact != nil || skipped.Labels["hash"] != "skipped_size" || skipped.Subject["size_bytes"] != int64(5<<30) {
		t.Fatalf("skipped: %+v %+v", skipped.Labels, skipped.Subject)
	}

	unavailable, _ := BuildEvent(EventInput{Action: ActionModify, DstPath: `C:\locked`, Volume: fixed,
		Hash: HashResult{Status: HashUnavailable}})
	if unavailable.Artifact != nil || unavailable.Labels["hash"] != "unavailable" {
		t.Fatalf("unavailable: %+v", unavailable.Labels)
	}
}

func TestRenameCarriesOldPathAndDeleteOmitsArtifact(t *testing.T) {
	rename, _ := BuildEvent(EventInput{Action: ActionRename, DstPath: `E:\new`, OldPath: `E:\old`, Volume: removable,
		Hash: HashResult{SHA256: "aa", Size: 3, Status: HashOK}})
	if rename.Subject["old_path"] != `E:\old` {
		t.Fatalf("rename: %+v", rename.Subject)
	}
	del, _ := BuildEvent(EventInput{Action: ActionDelete, DstPath: `E:\gone`, Volume: removable})
	if del.Artifact != nil || del.Subject["dst_path"] != `E:\gone` {
		t.Fatalf("delete: %+v", del)
	}
	if _, has := del.Subject["old_path"]; has {
		t.Fatalf("old_path только для rename: %+v", del.Subject)
	}
}
