//go:build windows

package netupload

import (
	"unsafe"

	"golang.org/x/sys/windows"
)

// ProcessFamily возвращает PID главного процесса приложения, к которому
// относится pid (см. familyRoot).
func ProcessFamily(pid uint32) uint32 {
	parents := parentTable()
	return familyRoot(pid,
		func(p uint32) (uint32, bool) { v, ok := parents[p]; return v, ok },
		func(p uint32) string {
			info := ProcessInfo(p)
			path, _ := info["path"].(string)
			return path
		})
}

func parentTable() map[uint32]uint32 {
	table := map[uint32]uint32{}
	snapshot, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return table
	}
	defer windows.CloseHandle(snapshot)
	var entry windows.ProcessEntry32
	entry.Size = uint32(unsafe.Sizeof(entry))
	for err = windows.Process32First(snapshot, &entry); err == nil; err = windows.Process32Next(snapshot, &entry) {
		table[entry.ProcessID] = entry.ParentProcessID
	}
	return table
}
