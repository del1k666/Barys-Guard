package netupload

import (
	"fmt"
	"testing"
	"time"
)

func TestReadsKeepRecentAndRefreshDuplicates(t *testing.T) {
	now := time.Unix(1_000, 0)
	reads := NewReads(time.Minute, 10, 10)

	reads.Add(7, Read{Path: `C:\a.pdf`, Size: 100, At: now})
	reads.Add(7, Read{Path: `C:\a.pdf`, Size: 100, At: now.Add(30 * time.Second)})
	reads.Add(7, Read{Path: `C:\b.pdf`, Size: 200, At: now.Add(40 * time.Second)})

	if got := reads.Recent(7, now); len(got) != 2 {
		t.Fatalf("повтор пути обновляет запись, а не плодит: %v", got)
	}
	// a.pdf обновлялся на 30-й секунде, b.pdf — на 40-й; с момента 35 с остаётся только b.pdf.
	if got := reads.Recent(7, now.Add(35*time.Second)); len(got) != 1 || got[0].Path != `C:\b.pdf` {
		t.Fatalf("устаревшее не отдаётся: %v", got)
	}
	if got := reads.Recent(8, now); len(got) != 0 {
		t.Fatalf("чужой процесс: %v", got)
	}
}

func TestReadsAreBounded(t *testing.T) {
	now := time.Unix(1_000, 0)
	reads := NewReads(time.Hour, 3, 2)

	for i := 0; i < 5; i++ {
		reads.Add(1, Read{Path: fmt.Sprintf(`C:\f%d.pdf`, i), Size: 10, At: now.Add(time.Duration(i) * time.Second)})
	}
	if got := reads.Recent(1, now); len(got) != 2 || got[0].Path != `C:\f3.pdf` {
		t.Fatalf("на процесс держится не больше 2 последних: %v", got)
	}

	for pid := uint32(10); pid < 20; pid++ {
		reads.Add(pid, Read{Path: `C:\x.pdf`, Size: 10, At: now.Add(time.Duration(pid) * time.Minute)})
	}
	if size := reads.Processes(); size > 3 {
		t.Fatalf("процессов %d при пределе 3", size)
	}
}

func TestReadsDropOutdatedByRetention(t *testing.T) {
	now := time.Unix(1_000, 0)
	reads := NewReads(time.Minute, 10, 10)

	reads.Add(1, Read{Path: `C:\old.pdf`, Size: 10, At: now})
	reads.Add(1, Read{Path: `C:\new.pdf`, Size: 10, At: now.Add(5 * time.Minute)})

	if got := reads.Recent(1, now.Add(-time.Hour)); len(got) != 1 || got[0].Path != `C:\new.pdf` {
		t.Fatalf("старше хранения выбрасывается: %v", got)
	}
}
