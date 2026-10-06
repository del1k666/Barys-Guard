package volumes

import (
	"encoding/binary"
	"strings"
)

// Шины STORAGE_BUS_TYPE, важные для классификации.
const busTypeUSB = 7

// ParseStorageDescriptor разбирает STORAGE_DEVICE_DESCRIPTOR, возвращённый
// IOCTL_STORAGE_QUERY_PROPERTY. Вынесено из Windows-кода, чтобы разбор
// проверялся на любой платформе и не паниковал на повреждённом ответе.
//
// Смещения: VendorIdOffset 12, ProductIdOffset 16, SerialNumberOffset 24,
// BusType 28; строки ASCII с завершающим нулём лежат дальше заголовка.
func ParseStorageDescriptor(data []byte) (bus, vendor, product, serial string) {
	if len(data) < 32 {
		return BusUnknown, "", "", ""
	}
	bus = BusOther
	if binary.LittleEndian.Uint32(data[28:32]) == busTypeUSB {
		bus = BusUSB
	}
	read := func(field int) string {
		offset := int(binary.LittleEndian.Uint32(data[field : field+4]))
		if offset <= 0 || offset >= len(data) {
			return ""
		}
		end := offset
		for end < len(data) && data[end] != 0 {
			end++
		}
		return strings.TrimSpace(string(data[offset:end]))
	}
	return bus, read(12), read(16), read(24)
}
