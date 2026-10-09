//go:build windows

package netupload

import "golang.org/x/sys/windows"

// ProcessInfo возвращает pid и путь образа процесса или nil, если процесс уже
// завершился или недоступен.
func ProcessInfo(pid uint32) map[string]any {
	handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return nil
	}
	defer windows.CloseHandle(handle)
	buffer := make([]uint16, windows.MAX_LONG_PATH)
	size := uint32(len(buffer))
	if err := windows.QueryFullProcessImageName(handle, 0, &buffer[0], &size); err != nil {
		return nil
	}
	return map[string]any{"pid": int(pid), "path": windows.UTF16ToString(buffer[:size])}
}
