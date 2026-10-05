package filewatch

import (
	"testing"

	"github.com/barysguard/agent/internal/volumes"
)

func TestVolumeForMatchesDriveLetterIgnoringCase(t *testing.T) {
	vols := []volumes.Volume{
		{DriveLetter: "C:", Type: volumes.TypeFixed},
		{DriveLetter: "E:", Type: volumes.TypeRemovable, Serial: "0781-5583"},
	}

	if got := VolumeFor(`e:\Отчёт 2026\a.xlsx`, vols); got.Serial != "0781-5583" {
		t.Fatalf("том: %+v", got)
	}
	if got := VolumeFor(`C:\Users\u\a.txt`, vols); got.Type != volumes.TypeFixed {
		t.Fatalf("том: %+v", got)
	}
	if got := VolumeFor(`Z:\none`, vols); got.Type != volumes.TypeUnknown {
		t.Fatalf("неизвестный том: %+v", got)
	}
	if got := VolumeFor(`\\server\share\a.txt`, vols); got.Type != volumes.TypeUnknown {
		t.Fatalf("UNC-путь: %+v", got)
	}
}
