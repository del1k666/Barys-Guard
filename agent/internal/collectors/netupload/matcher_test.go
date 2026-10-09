package netupload

import (
	"net/netip"
	"testing"
	"time"
)

var t0 = time.Unix(10_000, 0)

func newTestMatcher(t *testing.T) (*Matcher, *Reads, *Resolver) {
	t.Helper()
	cfg := DefaultConfig()
	reads := NewReads(cfg.Window, 100, 100)
	res := &Resolver{Catalog: NewCatalog(DefaultServices()), DNS: NewDNSCache(100)}
	res.DNS.Learn([]string{"content.dropboxapi.com"}, []netip.Addr{addr("162.125.1.14")}, time.Hour, t0)
	return NewMatcher(cfg, reads, res), reads, res
}

func send(pid uint32, to string, bytes uint64, at time.Time) Send {
	return Send{PID: pid, Addr: addr(to), Bytes: bytes, At: at}
}

func TestMatchWhenSentCoversTheFile(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 10_000, At: t0})

	got := m.Observe(send(5, "162.125.1.14", 10_500, t0.Add(2*time.Second)))

	if len(got) != 1 {
		t.Fatalf("ждали одно совпадение: %v", got)
	}
	match := got[0]
	if match.Read.Path != `C:\plan.pdf` || match.Service.Key != "dropbox" || match.Host != "content.dropboxapi.com" ||
		match.Confidence != ConfidenceHigh || match.Sent != 10_500 {
		t.Fatalf("совпадение: %+v", match)
	}
}

func TestMatchWithinToleranceIsMediumConfidence(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 10_000, At: t0})

	got := m.Observe(send(5, "162.125.1.14", 8_500, t0)) // 85 % — внутри допуска 20 %

	if len(got) != 1 || got[0].Confidence != ConfidenceMedium {
		t.Fatalf("%v", got)
	}
	if got := m.Observe(send(5, "162.125.1.14", 10, t0)); len(got) != 0 {
		t.Fatalf("тот же файл не должен засчитываться дважды: %v", got)
	}
}

func TestChunkedUploadProducesOneEvent(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 100_000, At: t0})

	total := 0
	for i := 0; i < 100; i++ {
		total += len(m.Observe(send(5, "162.125.1.14", 1_100, t0.Add(time.Duration(i)*100*time.Millisecond))))
	}
	if total != 1 {
		t.Fatalf("загрузка кусками дала %d событий, ждали 1", total)
	}
}

func TestNoMatchWhenTooLittleWasSent(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 100_000, At: t0})

	if got := m.Observe(send(5, "162.125.1.14", 2_000, t0)); len(got) != 0 {
		t.Fatalf("мелкий обмен не должен срабатывать: %v", got)
	}
}

func TestNoMatchForOtherProcessOrUnknownAddress(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 1_000, At: t0})

	if got := m.Observe(send(6, "162.125.1.14", 5_000, t0)); len(got) != 0 {
		t.Fatalf("другой процесс: %v", got)
	}
	if got := m.Observe(send(5, "8.8.8.8", 5_000, t0)); len(got) != 0 {
		t.Fatalf("адрес вне каталога: %v", got)
	}
}

func TestWindowExpiry(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 1_000, At: t0})

	if got := m.Observe(send(5, "162.125.1.14", 5_000, t0.Add(2*time.Minute))); len(got) != 0 {
		t.Fatalf("чтение старше окна: %v", got)
	}
}

func TestSeveralFilesShareTheSentBudget(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\big.pdf`, Size: 10_000, At: t0})
	reads.Add(5, Read{Path: `C:\mid.pdf`, Size: 5_000, At: t0})
	reads.Add(5, Read{Path: `C:\small.pdf`, Size: 4_000, At: t0})

	got := m.Observe(send(5, "162.125.1.14", 15_500, t0))

	// big (10 000) + mid (5 000) исчерпывают объём; на small (>= 3 200 нужно) остаётся 500.
	if len(got) != 2 || got[0].Read.Path != `C:\big.pdf` || got[1].Read.Path != `C:\mid.pdf` {
		t.Fatalf("%v", got)
	}
}

func TestInvalidToleranceDoesNotPanic(t *testing.T) {
	cfg := DefaultConfig()
	cfg.TolerancePercent = 200
	reads := NewReads(cfg.Window, 10, 10)
	res := &Resolver{Catalog: NewCatalog(DefaultServices()), DNS: NewDNSCache(10)}
	res.DNS.Learn([]string{"dropbox.com"}, []netip.Addr{addr("1.2.3.4")}, time.Hour, t0)
	m := NewMatcher(cfg, reads, res)
	reads.Add(1, Read{Path: `C:\a.pdf`, Size: 100, At: t0})

	m.Observe(send(1, "1.2.3.4", 1_000, t0))
}
