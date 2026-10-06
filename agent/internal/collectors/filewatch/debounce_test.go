package filewatch

import (
	"reflect"
	"testing"
	"time"
)

var t0 = time.Date(2026, 10, 5, 10, 0, 0, 0, time.UTC)

const (
	stable  = 1500 * time.Millisecond
	maxWait = 30 * time.Second
)

func at(ms int) time.Time { return t0.Add(time.Duration(ms) * time.Millisecond) }

func TestNothingSettlesWhileNotificationsKeepComing(t *testing.T) {
	d := NewDebouncer()
	d.Notify(`E:\a.bin`, OpCreate, at(0))
	d.Notify(`E:\a.bin`, OpModify, at(1000))
	d.Notify(`E:\a.bin`, OpModify, at(2000))

	if got := d.Settle(at(3000), stable, maxWait); len(got) != 0 {
		t.Fatalf("рано: %+v", got)
	}
	got := d.Settle(at(3600), stable, maxWait)
	if len(got) != 1 || got[0].Action != ActionCreate || got[0].Path != `E:\a.bin` {
		t.Fatalf("итог: %+v", got)
	}
	if again := d.Settle(at(9000), stable, maxWait); len(again) != 0 {
		t.Fatalf("событие вышло дважды: %+v", again)
	}
}

func TestMaxWaitForcesTheEventOutOfAnEndlessWrite(t *testing.T) {
	d := NewDebouncer()
	for ms := 0; ms <= 31000; ms += 1000 {
		d.Notify(`E:\big.iso`, OpModify, at(ms))
	}

	got := d.Settle(at(31000), stable, maxWait)

	if len(got) != 1 || got[0].Action != ActionModify {
		t.Fatalf("итог: %+v", got)
	}
}

func TestCreateThenDeleteInOneWindowProducesNothing(t *testing.T) {
	d := NewDebouncer()
	d.Notify(`C:\t\scratch.dat`, OpCreate, at(0))
	d.Notify(`C:\t\scratch.dat`, OpDelete, at(100))

	if got := d.Settle(at(5000), stable, maxWait); len(got) != 0 {
		t.Fatalf("временный файл дал событие: %+v", got)
	}
}

func TestDeleteOfAnExistingFile(t *testing.T) {
	d := NewDebouncer()
	d.Notify(`C:\d\old.txt`, OpDelete, at(0))

	got := d.Settle(at(2000), stable, maxWait)

	if len(got) != 1 || got[0].Action != ActionDelete {
		t.Fatalf("итог: %+v", got)
	}
}

func TestDeleteThenCreateIsAReplacementNotACreate(t *testing.T) {
	// Так сохраняют файлы редакторы: старый удаляется, новый создаётся.
	d := NewDebouncer()
	d.Notify(`C:\d\doc.docx`, OpDelete, at(0))
	d.Notify(`C:\d\doc.docx`, OpCreate, at(50))

	got := d.Settle(at(2000), stable, maxWait)

	if len(got) != 1 || got[0].Action != ActionModify {
		t.Fatalf("итог: %+v", got)
	}
}

func TestRenameKeepsBothPaths(t *testing.T) {
	d := NewDebouncer()
	d.NotifyRename(`E:\old.xlsx`, `E:\new.xlsx`, at(0))

	got := d.Settle(at(2000), stable, maxWait)

	want := []Settled{{Path: `E:\new.xlsx`, OldPath: `E:\old.xlsx`, Action: ActionRename}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("итог: %+v", got)
	}
}

func TestRenameOfAFileCreatedInTheSameWindowIsACreate(t *testing.T) {
	// Word пишет ~WRD0001.tmp и переименовывает его в документ.
	d := NewDebouncer()
	d.Notify(`C:\d\~WRD0001.tmp`, OpCreate, at(0))
	d.NotifyRename(`C:\d\~WRD0001.tmp`, `C:\d\report.docx`, at(100))

	got := d.Settle(at(2000), stable, maxWait)

	if len(got) != 1 || got[0].Action != ActionCreate || got[0].Path != `C:\d\report.docx` || got[0].OldPath != "" {
		t.Fatalf("итог: %+v", got)
	}
}

func TestPathsAreCaseInsensitiveButKeepTheirSpelling(t *testing.T) {
	d := NewDebouncer()
	d.Notify(`C:\Docs\Отчёт.docx`, OpCreate, at(0))
	d.Notify(`c:\docs\отчёт.DOCX`, OpModify, at(10))

	got := d.Settle(at(2000), stable, maxWait)

	if len(got) != 1 {
		t.Fatalf("регистр размножил события: %+v", got)
	}
}

func TestSettledOrderIsDeterministic(t *testing.T) {
	d := NewDebouncer()
	d.Notify(`E:\b`, OpCreate, at(10))
	d.Notify(`E:\a`, OpCreate, at(0))

	got := d.Settle(at(5000), stable, maxWait)

	if len(got) != 2 || got[0].Path != `E:\a` || got[1].Path != `E:\b` {
		t.Fatalf("порядок: %+v", got)
	}
}
