//go:build windows

package volumes

import (
	"fmt"
	"unsafe"

	"golang.org/x/sys/windows"
)

const (
	ioctlStorageQueryProperty = 0x002D1400
	storageDeviceProperty     = 0
	propertyStandardQuery     = 0
)

// storagePropertyQuery — STORAGE_PROPERTY_QUERY; размер 12 байт, как в C.
type storagePropertyQuery struct {
	PropertyID           uint32
	QueryType            uint32
	AdditionalParameters [1]byte
}

type winProvider struct{}

func NewProvider() Provider { return winProvider{} }

func driveTypeName(driveType uint32) string {
	switch driveType {
	case windows.DRIVE_REMOVABLE:
		return "removable"
	case windows.DRIVE_FIXED:
		return "fixed"
	case windows.DRIVE_REMOTE:
		return "remote"
	case windows.DRIVE_CDROM:
		return "cdrom"
	}
	return ""
}

func (winProvider) Snapshot() ([]Volume, error) {
	mask, err := windows.GetLogicalDrives()
	if err != nil {
		return nil, fmt.Errorf("GetLogicalDrives: %w", err)
	}

	var volumes []Volume
	for i := 0; i < 26; i++ {
		if mask&(1<<uint(i)) == 0 {
			continue
		}
		letter := string(rune('A'+i)) + ":"
		root, err := windows.UTF16PtrFromString(letter + `\`)
		if err != nil {
			continue
		}

		driveType := driveTypeName(windows.GetDriveType(root))
		if driveType == "" || driveType == "cdrom" {
			continue
		}

		label := make([]uint16, 261)
		fsName := make([]uint16, 261)
		var serial uint32
		// Не готовый к чтению том (пустой картридер) пропускается: данных
		// о нём нет, и он появится при вставке носителя.
		if err := windows.GetVolumeInformation(root, &label[0], uint32(len(label)), &serial, nil, nil, &fsName[0], uint32(len(fsName))); err != nil {
			continue
		}

		volume := Volume{
			DriveLetter: letter,
			Serial:      fmt.Sprintf("%04X-%04X", serial>>16, serial&0xFFFF),
			Label:       windows.UTF16ToString(label),
			FS:          windows.UTF16ToString(fsName),
			Bus:         BusUnknown,
		}

		var free, total, totalFree uint64
		if err := windows.GetDiskFreeSpaceEx(root, &free, &total, &totalFree); err == nil {
			volume.SizeBytes = int64(total)
		}

		if driveType != "remote" {
			bus, vendor, product, deviceSerial := queryStorage(letter)
			volume.Bus, volume.Vendor, volume.Product, volume.DeviceSerial = bus, vendor, product, deviceSerial
		}
		volume.Type = ClassifyType(driveType, volume.Bus)
		volumes = append(volumes, volume)
	}
	return volumes, nil
}

// queryStorage спрашивает у устройства шину и описание. Открытие тома без прав
// на чтение не требует администратора; при отказе возвращается «неизвестно»,
// и том классифицируется только по типу диска.
func queryStorage(letter string) (bus, vendor, product, serial string) {
	path, err := windows.UTF16PtrFromString(`\\.\` + letter)
	if err != nil {
		return BusUnknown, "", "", ""
	}
	handle, err := windows.CreateFile(path, 0, windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE,
		nil, windows.OPEN_EXISTING, 0, 0)
	if err != nil {
		return BusUnknown, "", "", ""
	}
	defer windows.CloseHandle(handle)

	query := storagePropertyQuery{PropertyID: storageDeviceProperty, QueryType: propertyStandardQuery}
	out := make([]byte, 1024)
	var returned uint32
	err = windows.DeviceIoControl(handle, ioctlStorageQueryProperty,
		(*byte)(unsafe.Pointer(&query)), uint32(unsafe.Sizeof(query)),
		&out[0], uint32(len(out)), &returned, nil)
	if err != nil {
		return BusUnknown, "", "", ""
	}
	return ParseStorageDescriptor(out[:returned])
}
