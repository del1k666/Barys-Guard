//go:build windows

package filewatch

import (
	"context"
	"errors"
	"time"

	"golang.org/x/sys/windows"
)

// ErrUnsupported совпадает по смыслу с заглушкой других платформ; на Windows
// не возвращается, но нужен для единообразного кода сборщика.
var ErrUnsupported = errors.New("наблюдение за файлами не поддерживается на этой платформе")

// StartWatcher наблюдает за корнем root, пока не отменён ctx, и передаёт
// разобранные уведомления в out; overflow сообщает о переполнении буфера ОС.
type StartWatcher func(ctx context.Context, root string, out chan<- Raw, overflow func(root string)) error

const (
	watchBufferSize = 64 * 1024
	watchMask       = windows.FILE_NOTIFY_CHANGE_FILE_NAME | windows.FILE_NOTIFY_CHANGE_DIR_NAME |
		windows.FILE_NOTIFY_CHANGE_SIZE | windows.FILE_NOTIFY_CHANGE_LAST_WRITE
)

// DefaultStartWatcher — блокирующий цикл ReadDirectoryChangesW на корень.
// Каждый корень получает собственную горутину: цикл заблокирован в системном
// вызове, остановить его можно только отменой ввода-вывода.
var DefaultStartWatcher StartWatcher = func(ctx context.Context, root string, out chan<- Raw, overflow func(string)) error {
	path, err := windows.UTF16PtrFromString(root)
	if err != nil {
		return err
	}
	handle, err := windows.CreateFile(path, windows.FILE_LIST_DIRECTORY,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE|windows.FILE_SHARE_DELETE,
		nil, windows.OPEN_EXISTING, windows.FILE_FLAG_BACKUP_SEMANTICS, 0)
	if err != nil {
		return err
	}

	done := make(chan struct{})
	defer func() {
		close(done)
		windows.CloseHandle(handle)
	}()
	// Отмена, пришедшая раньше, чем вызов встал на ожидание, ни во что бы не
	// упёрлась и осталась бы незамеченной: поэтому отмена повторяется, пока
	// цикл не завершится.
	go func() {
		select {
		case <-ctx.Done():
		case <-done:
			return
		}
		for {
			windows.CancelIoEx(handle, nil)
			select {
			case <-done:
				return
			case <-time.After(50 * time.Millisecond):
			}
		}
	}()

	buffer := make([]byte, watchBufferSize)
	for {
		if ctx.Err() != nil {
			return nil
		}
		var returned uint32
		err := windows.ReadDirectoryChanges(handle, &buffer[0], uint32(len(buffer)), true, watchMask, &returned, nil, 0)
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		if returned == 0 {
			// Буфер переполнился: система выбросила изменения. Сообщаем и
			// продолжаем; повторного сканирования аудит не требует.
			overflow(root)
			continue
		}
		for _, raw := range ParseNotifications(root, buffer[:returned]) {
			select {
			case out <- raw:
			case <-ctx.Done():
				return nil
			}
		}
	}
}
