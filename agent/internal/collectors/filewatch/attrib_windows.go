//go:build windows

package filewatch

import (
	"os"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	rstrtmgr       = windows.NewLazySystemDLL("rstrtmgr.dll")
	procRmStart    = rstrtmgr.NewProc("RmStartSession")
	procRmRegister = rstrtmgr.NewProc("RmRegisterResources")
	procRmGetList  = rstrtmgr.NewProc("RmGetList")
	procRmEnd      = rstrtmgr.NewProc("RmEndSession")
)

const (
	cchRmSessionKey = 32
	errorMoreData   = 234
	maxHolders      = 16
)

type rmUniqueProcess struct {
	ProcessID uint32
	StartTime windows.Filetime
}

type rmProcessInfo struct {
	Process          rmUniqueProcess
	AppName          [256]uint16
	ServiceShortName [64]uint16
	ApplicationType  uint32
	AppStatus        uint32
	TSSessionID      uint32
	Restartable      int32
}

// rmAttributor определяет процесс через Restart Manager: он перечисляет
// процессы, удерживающие файл открытым. Пока идёт копирование, это копирующий
// процесс; после закрытия дескриптора ответ пуст — тогда событие честно
// помечается «процесс не определён». self — PID самого агента: он открывает
// файл для хеширования и не должен определять сам себя.
type rmAttributor struct{ self int }

func NewAttributor() Attributor { return &rmAttributor{self: os.Getpid()} }

func (a *rmAttributor) Attribute(path string) (map[string]any, bool) {
	var session uint32
	key := make([]uint16, cchRmSessionKey+1)
	if result, _, _ := procRmStart.Call(uintptr(unsafe.Pointer(&session)), 0, uintptr(unsafe.Pointer(&key[0]))); result != 0 {
		return nil, false
	}
	defer procRmEnd.Call(uintptr(session))

	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return nil, false
	}
	if result, _, _ := procRmRegister.Call(uintptr(session), 1, uintptr(unsafe.Pointer(&name)), 0, 0, 0, 0); result != 0 {
		return nil, false
	}

	infos := make([]rmProcessInfo, maxHolders)
	var needed, count, reasons uint32
	count = uint32(len(infos))
	result, _, _ := procRmGetList.Call(uintptr(session), uintptr(unsafe.Pointer(&needed)),
		uintptr(unsafe.Pointer(&count)), uintptr(unsafe.Pointer(&infos[0])), uintptr(unsafe.Pointer(&reasons)))
	if result == errorMoreData && needed > 0 {
		infos = make([]rmProcessInfo, needed)
		count = needed
		result, _, _ = procRmGetList.Call(uintptr(session), uintptr(unsafe.Pointer(&needed)),
			uintptr(unsafe.Pointer(&count)), uintptr(unsafe.Pointer(&infos[0])), uintptr(unsafe.Pointer(&reasons)))
	}
	if result != 0 {
		return nil, false
	}

	for _, info := range infos[:count] {
		pid := int(info.Process.ProcessID)
		if pid == 0 || pid == 4 || pid == a.self {
			continue
		}
		return map[string]any{"pid": pid, "path": imagePath(uint32(pid), windows.UTF16ToString(info.AppName[:]))}, true
	}
	return nil, false
}

// imagePath возвращает полный путь образа процесса; при отказе в доступе
// (процесс повышенный) остаётся имя приложения от Restart Manager.
func imagePath(pid uint32, fallback string) string {
	handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return fallback
	}
	defer windows.CloseHandle(handle)
	buffer := make([]uint16, windows.MAX_LONG_PATH)
	size := uint32(len(buffer))
	if err := windows.QueryFullProcessImageName(handle, 0, &buffer[0], &size); err != nil {
		return fallback
	}
	return windows.UTF16ToString(buffer[:size])
}
