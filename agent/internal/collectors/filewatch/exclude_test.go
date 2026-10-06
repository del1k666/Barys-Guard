package filewatch

import "testing"

func TestExcluderMatchesWindowsStyleGlobs(t *testing.T) {
	excluder := NewExcluder([]string{`*\~$*`, `*.tmp`, `*.crdownload`, `*\AppData\*`, `C:\ProgramData\BarysGuard\*`})

	hits := []string{
		`C:\Users\u\Documents\~$report.docx`,
		`C:\Users\u\Downloads\file.TMP`,
		`C:\Users\u\Downloads\movie.mkv.crdownload`,
		`C:\Users\u\appdata\Local\x.dat`,
		`C:\ProgramData\BarysGuard\events.db`,
	}
	for _, path := range hits {
		if !excluder.Match(path) {
			t.Errorf("%q должен исключаться", path)
		}
	}
	misses := []string{
		`C:\Users\u\Documents\отчёт.docx`,
		`E:\report.xlsx`,
		`C:\Users\u\Documents\tmp-notes.txt`,
	}
	for _, path := range misses {
		if excluder.Match(path) {
			t.Errorf("%q не должен исключаться", path)
		}
	}
}

func TestEmptyExcluderMatchesNothing(t *testing.T) {
	if NewExcluder(nil).Match(`C:\x`) {
		t.Fatal("пустой список ничего не исключает")
	}
}

func TestQuestionMarkMatchesOneCharacter(t *testing.T) {
	excluder := NewExcluder([]string{`*.t?t`})
	if !excluder.Match(`C:\a.txt`) || excluder.Match(`C:\a.tt`) {
		t.Fatal("? должен совпадать ровно с одним символом")
	}
}
