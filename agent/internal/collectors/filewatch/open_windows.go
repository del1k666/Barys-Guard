//go:build windows

package filewatch

import (
	"io"
	"os"

	"golang.org/x/sys/windows"
)

// openFile открывает файл для хеширования с полным общим доступом, включая
// удаление и переименование. os.Open на Windows не даёт FILE_SHARE_DELETE:
// пока агент хеширует файл, пользователь не смог бы удалить или переместить
// его в проводнике, а Office не смог бы заменить файл при сохранении.
func openFile(path string) (io.ReadCloser, int64, error) {
	// Каталог без FILE_FLAG_BACKUP_SEMANTICS не открывается («доступ запрещён»),
	// и это выглядело бы как отказ в доступе, а не как «это каталог».
	info, err := os.Stat(path)
	if err != nil {
		return nil, 0, err
	}
	if info.IsDir() {
		return nil, 0, errIsDirectory
	}

	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return nil, 0, err
	}
	handle, err := windows.CreateFile(name, windows.GENERIC_READ,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE|windows.FILE_SHARE_DELETE,
		nil, windows.OPEN_EXISTING, windows.FILE_ATTRIBUTE_NORMAL|windows.FILE_FLAG_SEQUENTIAL_SCAN, 0)
	if err != nil {
		return nil, 0, err
	}
	file := os.NewFile(uintptr(handle), path)
	return file, info.Size(), nil
}
