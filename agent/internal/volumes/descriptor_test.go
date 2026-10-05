package volumes

import (
	"encoding/binary"
	"testing"
)

// descriptor строит STORAGE_DEVICE_DESCRIPTOR с тремя строками за заголовком.
func descriptor(bus uint32, vendor, product, serial string) []byte {
	buf := make([]byte, 40)
	put := func(offsetField int, text string) {
		if text == "" {
			return
		}
		binary.LittleEndian.PutUint32(buf[offsetField:], uint32(len(buf)))
		buf = append(buf, []byte(text)...)
		buf = append(buf, 0)
	}
	binary.LittleEndian.PutUint32(buf[28:], bus)
	put(12, vendor)
	put(16, product)
	put(24, serial)
	return buf
}

func TestParseStorageDescriptorReadsBusAndStrings(t *testing.T) {
	bus, vendor, product, serial := ParseStorageDescriptor(descriptor(7, "Kingston ", "DataTraveler 3.0 ", " 0019E06B "))

	if bus != BusUSB || vendor != "Kingston" || product != "DataTraveler 3.0" || serial != "0019E06B" {
		t.Fatalf("bus=%q vendor=%q product=%q serial=%q", bus, vendor, product, serial)
	}
}

func TestParseStorageDescriptorMapsOtherBuses(t *testing.T) {
	if bus, _, _, _ := ParseStorageDescriptor(descriptor(11, "", "", "")); bus != BusOther {
		t.Fatalf("SATA = %q", bus)
	}
}

func TestParseStorageDescriptorSurvivesGarbage(t *testing.T) {
	for _, data := range [][]byte{nil, {1, 2, 3}, make([]byte, 40)} {
		bus, _, _, _ := ParseStorageDescriptor(data)
		if bus != BusUnknown && bus != BusOther {
			t.Errorf("мусор дал шину %q", bus)
		}
	}

	// Смещение строки за пределами буфера не должно давать панику.
	bad := make([]byte, 40)
	binary.LittleEndian.PutUint32(bad[12:], 9999)
	ParseStorageDescriptor(bad)
}
