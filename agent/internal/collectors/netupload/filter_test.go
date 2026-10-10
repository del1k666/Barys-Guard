package netupload

import "testing"

func TestFilterPath(t *testing.T) {
	f := NewFilter(DefaultConfig())

	for _, path := range []string{`C:\Users\a\Documents\План.PDF`, `E:\report.docx`, `D:\x\archive.zip`} {
		if !f.PathOK(path) {
			t.Errorf("%q должен быть кандидатом", path)
		}
	}
	for _, path := range []string{
		`C:\Users\a\Documents\photo.jpg`,
		`C:\Users\a\Documents\noext`,
		`C:\Users\a\AppData\Local\x\cache.pdf`,
		`C:\Windows\System32\help.pdf`,
		`C:\Program Files\App\readme.txt`,
		`C:\ProgramData\x\a.csv`,
		`C:\$Recycle.Bin\S-1\a.pdf`,
		`C:\Users\a\Documents\dir.v2\file`,
	} {
		if f.PathOK(path) {
			t.Errorf("%q не должен быть кандидатом", path)
		}
	}
}

func TestFilterSize(t *testing.T) {
	cfg := DefaultConfig()
	cfg.MinFileBytes = 100
	cfg.MaxFileBytes = 1000
	f := NewFilter(cfg)

	cases := map[int64]bool{99: false, 100: true, 1000: true, 1001: false, 0: false}
	for size, want := range cases {
		if got := f.SizeOK(size); got != want {
			t.Errorf("SizeOK(%d) = %v, ждали %v", size, got, want)
		}
	}
}
