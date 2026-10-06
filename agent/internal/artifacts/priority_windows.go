// agent/internal/artifacts/priority_windows.go
//go:build windows

package artifacts

import (
	"runtime"
	"syscall"
)

var (
	kernel32              = syscall.NewLazyDLL("kernel32.dll")
	procGetCurrentThread  = kernel32.NewProc("GetCurrentThread")
	procSetThreadPriority = kernel32.NewProc("SetThreadPriority")
)

const (
	threadModeBackgroundBegin = 0x00010000
	threadModeBackgroundEnd   = 0x00020000
)

// enterBackground переводит поток воркера в фоновый режим Windows: ниже
// приоритет процессора, ввода-вывода и памяти, чем у программ пользователя.
// Горутина закрепляется за потоком; возвращённая функция возвращает всё назад.
func enterBackground() func() {
	runtime.LockOSThread()
	handle, _, _ := procGetCurrentThread.Call()
	ok, _, _ := procSetThreadPriority.Call(handle, threadModeBackgroundBegin)
	if ok == 0 {
		runtime.UnlockOSThread()
		return func() {}
	}
	return func() {
		procSetThreadPriority.Call(handle, threadModeBackgroundEnd)
		runtime.UnlockOSThread()
	}
}
