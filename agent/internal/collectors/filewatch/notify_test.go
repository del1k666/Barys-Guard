package filewatch

import (
	"encoding/binary"
	"reflect"
	"strings"
	"testing"
	"unicode/utf16"
)

type notification struct {
	action uint32
	name   string
}

// buildBuffer собирает FILE_NOTIFY_INFORMATION как отдаёт ReadDirectoryChangesW.
func buildBuffer(items ...notification) []byte {
	var buf []byte
	for i, item := range items {
		name := utf16.Encode([]rune(item.name))
		size := 12 + len(name)*2
		padded := (size + 3) &^ 3
		record := make([]byte, padded)
		next := uint32(padded)
		if i == len(items)-1 {
			next = 0
		}
		binary.LittleEndian.PutUint32(record[0:], next)
		binary.LittleEndian.PutUint32(record[4:], item.action)
		binary.LittleEndian.PutUint32(record[8:], uint32(len(name)*2))
		for j, unit := range name {
			binary.LittleEndian.PutUint16(record[12+j*2:], unit)
		}
		buf = append(buf, record...)
	}
	return buf
}

func TestParseSimpleActions(t *testing.T) {
	buf := buildBuffer(
		notification{1, `Отчёт 2026.xlsx`},
		notification{3, `Отчёт 2026.xlsx`},
		notification{2, `sub\old.txt`},
	)

	got := ParseNotifications(`E:\`, buf)

	want := []Raw{
		{Kind: Created, Path: `E:\Отчёт 2026.xlsx`},
		{Kind: Modified, Path: `E:\Отчёт 2026.xlsx`},
		{Kind: Deleted, Path: `E:\sub\old.txt`},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("разбор: %+v", got)
	}
}

func TestParsePairsRenames(t *testing.T) {
	buf := buildBuffer(notification{4, "old.txt"}, notification{5, "new.txt"})

	got := ParseNotifications(`C:\Users\u\Documents`, buf)

	want := []Raw{{Kind: Renamed, Path: `C:\Users\u\Documents\new.txt`, OldPath: `C:\Users\u\Documents\old.txt`}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("разбор: %+v", got)
	}
}

func TestUnpairedRenameHalvesDegradeToDeleteAndCreate(t *testing.T) {
	// Файл переименован из наблюдаемой папки наружу или обратно.
	got := ParseNotifications(`C:\d`, buildBuffer(notification{4, "gone.txt"}))
	if len(got) != 1 || got[0].Kind != Deleted || got[0].Path != `C:\d\gone.txt` {
		t.Fatalf("OLD без NEW: %+v", got)
	}
	got = ParseNotifications(`C:\d`, buildBuffer(notification{5, "came.txt"}))
	if len(got) != 1 || got[0].Kind != Created || got[0].Path != `C:\d\came.txt` {
		t.Fatalf("NEW без OLD: %+v", got)
	}
}

func TestLongNamesAreParsedWithoutTruncation(t *testing.T) {
	name := strings.Repeat("вложенная-папка-", 25) + `файл.txt` // длиннее 260 символов
	got := ParseNotifications(`C:\d`, buildBuffer(notification{1, name}))

	if len(got) != 1 || got[0].Path != `C:\d\`+name {
		t.Fatalf("длинное имя: %d записей", len(got))
	}
}

func TestMalformedBuffersDoNotPanic(t *testing.T) {
	valid := buildBuffer(notification{1, "a.txt"})
	truncated := valid[:10]
	oversized := append([]byte(nil), valid...)
	binary.LittleEndian.PutUint32(oversized[8:], 9999)
	badNext := append([]byte(nil), valid...)
	binary.LittleEndian.PutUint32(badNext[0:], 1<<30)

	for _, data := range [][]byte{nil, {1, 2, 3}, truncated, oversized, badNext} {
		ParseNotifications(`C:\d`, data)
	}
}

func TestRootWithTrailingBackslashIsNotDoubled(t *testing.T) {
	got := ParseNotifications(`E:\`, buildBuffer(notification{1, "a.txt"}))
	if got[0].Path != `E:\a.txt` {
		t.Fatalf("путь: %q", got[0].Path)
	}
}
