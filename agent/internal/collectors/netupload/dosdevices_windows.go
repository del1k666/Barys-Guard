//go:build windows

package netupload

import (
	"strings"
	"sync"
	"time"

	"golang.org/x/sys/windows"
)

const dosRefreshEvery = 5 * time.Second

// queryDosDevices строит карту «устройство → буква диска» для всех букв A..Z.
func queryDosDevices() map[string]string {
	devices := map[string]string{}
	buffer := make([]uint16, 1024)
	for letter := 'A'; letter <= 'Z'; letter++ {
		drive := string(letter) + ":"
		name, err := windows.UTF16PtrFromString(drive)
		if err != nil {
			continue
		}
		n, err := windows.QueryDosDevice(name, &buffer[0], uint32(len(buffer)))
		if err != nil || n == 0 {
			continue
		}
		target := windows.UTF16ToString(buffer[:n])
		devices[strings.ToLower(target)] = drive
	}
	return devices
}

// dosMap переводит NT-пути в DOS. Новый том (флешка) появляется между
// запросами, поэтому при промахе карта обновляется, но не чаще раза в 5 секунд.
type dosMap struct {
	mu        sync.Mutex
	devices   map[string]string
	refreshed time.Time
}

func newDosMap() *dosMap {
	return &dosMap{devices: queryDosDevices(), refreshed: time.Now()}
}

func (d *dosMap) toDOS(path string) string {
	d.mu.Lock()
	defer d.mu.Unlock()
	if dos, ok := ntToDOS(path, d.devices); ok {
		return dos
	}
	if time.Since(d.refreshed) > dosRefreshEvery {
		d.devices = queryDosDevices()
		d.refreshed = time.Now()
		if dos, ok := ntToDOS(path, d.devices); ok {
			return dos
		}
	}
	return ""
}
