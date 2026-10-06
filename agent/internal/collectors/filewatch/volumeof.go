package filewatch

import (
	"strings"

	"github.com/barysguard/agent/internal/volumes"
)

// VolumeFor находит том по букве диска в начале пути. Сетевые пути (\\server\share)
// и неизвестные буквы дают том неизвестного типа: событие не теряется,
// просто без серийного номера.
func VolumeFor(path string, vols []volumes.Volume) volumes.Volume {
	if len(path) >= 2 && path[1] == ':' {
		letter := path[:2]
		for _, volume := range vols {
			if strings.EqualFold(volume.DriveLetter, letter) {
				return volume
			}
		}
	}
	return volumes.Volume{Type: volumes.TypeUnknown}
}
