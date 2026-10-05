package volumes

import (
	"errors"
	"testing"
)

type fakeProvider struct {
	vols []Volume
	err  error
}

func (f *fakeProvider) Snapshot() ([]Volume, error) { return f.vols, f.err }

func usb(letter, serial string) Volume {
	return Volume{DriveLetter: letter, Serial: serial, Type: TypeRemovable, Bus: BusUSB, Label: "K"}
}

func drain(ch <-chan Change) []Change {
	var out []Change
	for {
		select {
		case c := <-ch:
			out = append(out, c)
		default:
			return out
		}
	}
}

func TestFirstPollReportsEveryMountedVolume(t *testing.T) {
	provider := &fakeProvider{vols: []Volume{usb("E:", "A"), {DriveLetter: "C:", Serial: "S", Type: TypeFixed}}}
	hub := NewHub(provider, 0)
	changes, cancel := hub.Subscribe()
	defer cancel()

	if err := hub.Poll(); err != nil {
		t.Fatal(err)
	}

	got := drain(changes)
	// Агент, запущенный с вставленной флешкой, должен её увидеть.
	if len(got) != 2 || !got[0].Mounted || !got[1].Mounted {
		t.Fatalf("изменения: %+v", got)
	}
}

func TestLateSubscriberGetsReplayOfCurrentVolumes(t *testing.T) {
	provider := &fakeProvider{vols: []Volume{usb("E:", "A")}}
	hub := NewHub(provider, 0)
	hub.Poll()

	changes, cancel := hub.Subscribe()
	defer cancel()

	got := drain(changes)
	if len(got) != 1 || !got[0].Mounted || got[0].Volume.DriveLetter != "E:" {
		t.Fatalf("повтор: %+v", got)
	}
}

func TestEjectAndMediaSwapInTheSameLetter(t *testing.T) {
	provider := &fakeProvider{vols: []Volume{usb("E:", "A")}}
	hub := NewHub(provider, 0)
	changes, cancel := hub.Subscribe()
	defer cancel()
	hub.Poll()
	drain(changes)

	// Другая флешка в той же букве: это и unmount, и mount, а не тишина.
	provider.vols = []Volume{usb("E:", "B")}
	hub.Poll()
	got := drain(changes)
	if len(got) != 2 || got[0].Mounted || got[0].Volume.Serial != "A" || !got[1].Mounted || got[1].Volume.Serial != "B" {
		t.Fatalf("смена носителя: %+v", got)
	}

	provider.vols = nil
	hub.Poll()
	got = drain(changes)
	if len(got) != 1 || got[0].Mounted {
		t.Fatalf("извлечение: %+v", got)
	}
}

func TestFailedSnapshotKeepsKnownVolumes(t *testing.T) {
	provider := &fakeProvider{vols: []Volume{usb("E:", "A")}}
	hub := NewHub(provider, 0)
	changes, cancel := hub.Subscribe()
	defer cancel()
	hub.Poll()
	drain(changes)

	provider.err = errors.New("сбой")
	if err := hub.Poll(); err == nil {
		t.Fatal("ошибка снимка не возвращена")
	}

	// Сбой опроса не должен превращаться в «все носители извлечены».
	if got := drain(changes); len(got) != 0 {
		t.Fatalf("при сбое пришли изменения: %+v", got)
	}
	if len(hub.Current()) != 1 {
		t.Fatal("известные тома потеряны")
	}
}

func TestUnsubscribeClosesTheChannel(t *testing.T) {
	hub := NewHub(&fakeProvider{}, 0)
	changes, cancel := hub.Subscribe()

	cancel()
	cancel() // повторная отмена безопасна

	if _, ok := <-changes; ok {
		t.Fatal("канал не закрыт")
	}
	if err := hub.Poll(); err != nil {
		t.Fatal(err)
	}
}

func TestClassifyType(t *testing.T) {
	cases := []struct{ driveType, bus, want string }{
		{"removable", BusOther, TypeRemovable},
		{"removable", BusUSB, TypeRemovable},
		// Внешний жёсткий диск Windows показывает как фиксированный.
		{"fixed", BusUSB, TypeRemovable},
		{"fixed", BusOther, TypeFixed},
		{"fixed", BusUnknown, TypeFixed},
		{"remote", BusUnknown, TypeNetwork},
		{"cdrom", BusUnknown, TypeUnknown},
		{"", BusUnknown, TypeUnknown},
	}
	for _, c := range cases {
		if got := ClassifyType(c.driveType, c.bus); got != c.want {
			t.Errorf("ClassifyType(%q, %q) = %q, ожидалось %q", c.driveType, c.bus, got, c.want)
		}
	}
}
