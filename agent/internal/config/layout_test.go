package config

import (
	"path/filepath"
	"testing"
)

func TestStagingDirIsInsideTheDataDir(t *testing.T) {
	layout := NewLayout(filepath.Join("data", "agent"))
	if got, want := layout.StagingDir(), filepath.Join("data", "agent", "staging"); got != want {
		t.Fatalf("StagingDir = %q, ожидалось %q", got, want)
	}
}
