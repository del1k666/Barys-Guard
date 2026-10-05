//go:build windows

package volumes

import (
	"os"
	"strings"
	"testing"
)

func TestSnapshotContainsTheSystemDrive(t *testing.T) {
	snapshot, err := NewProvider().Snapshot()
	if err != nil {
		t.Fatalf("Snapshot: %v", err)
	}

	system := strings.ToUpper(os.Getenv("SystemDrive"))
	for _, volume := range snapshot {
		if volume.DriveLetter == system {
			if volume.SizeBytes <= 0 || volume.FS == "" {
				t.Fatalf("системный том описан неполно: %+v", volume)
			}
			if volume.Type != TypeFixed && volume.Type != TypeRemovable {
				t.Fatalf("тип системного тома %q", volume.Type)
			}
			return
		}
	}
	t.Fatalf("системный диск %s не найден среди %d томов", system, len(snapshot))
}

func TestSnapshotReportsLettersInDriveFormat(t *testing.T) {
	snapshot, _ := NewProvider().Snapshot()
	for _, volume := range snapshot {
		if len(volume.DriveLetter) != 2 || volume.DriveLetter[1] != ':' {
			t.Fatalf("буква %q не в формате «C:»", volume.DriveLetter)
		}
	}
}
