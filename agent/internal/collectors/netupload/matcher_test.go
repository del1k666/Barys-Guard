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
	reads := NewReads(10*time.Minute, 100, 100)
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
	reads := NewReads(10*time.Minute, 10, 10)
	res := &Resolver{Catalog: NewCatalog(DefaultServices()), DNS: NewDNSCache(10)}
	res.DNS.Learn([]string{"dropbox.com"}, []netip.Addr{addr("1.2.3.4")}, time.Hour, t0)
	m := NewMatcher(cfg, reads, res)
	reads.Add(1, Read{Path: `C:\a.pdf`, Size: 100, At: t0})

	m.Observe(send(1, "1.2.3.4", 1_000, t0))
}

func TestBigSlowUploadIsReportedOnceAt80Percent(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:big.bin`, Size: 100_000_000, At: t0})

	var matchedAt []int
	var got []Match
	for i := 0; i < 100; i++ {
		found := m.Observe(send(5, "162.125.1.14", 1_000_000, t0.Add(time.Duration(i)*time.Second)))
		if len(found) > 0 {
			matchedAt = append(matchedAt, i+1)
			got = append(got, found...)
		}
	}
	if len(matchedAt) != 1 || matchedAt[0] != 80 {
		t.Fatalf("ждали одно совпадение на 80-м отправлении: %v", matchedAt)
	}
	if got[0].Confidence != ConfidenceMedium || got[0].Sent != 80_000_000 {
		t.Fatalf("%+v", got[0])
	}
}

func TestContinuousUploadNeverReportsTwice(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	window := DefaultConfig().Window
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 1_000_000, At: t0})

	total := 0
	for i := 0; i < int((4*window)/time.Second); i++ {
		total += len(m.Observe(send(5, "162.125.1.14", 100_000, t0.Add(time.Duration(i)*time.Second))))
	}
	if total != 1 {
		t.Fatalf("длинная загрузка дала %d событий, ждали 1", total)
	}
}

func TestSameFileAfterIdleGapIsReportedAgain(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	window := DefaultConfig().Window
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 10_000, At: t0})
	if got := m.Observe(send(5, "162.125.1.14", 10_000, t0)); len(got) != 1 {
		t.Fatalf("первая загрузка: %v", got)
	}

	again := t0.Add(2 * window)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 10_000, At: again})
	if got := m.Observe(send(5, "162.125.1.14", 10_000, again)); len(got) != 1 {
		t.Fatalf("повторная загрузка после простоя: %v", got)
	}
}

func TestReadOlderThanWindowBeforeFirstSendDoesNotMatch(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	window := DefaultConfig().Window
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 1_000, At: t0})

	if got := m.Observe(send(5, "162.125.1.14", 5_000, t0.Add(window+time.Second))); len(got) != 0 {
		t.Fatalf("чтение раньше окна до начала передачи: %v", got)
	}
}

func TestChattyClientDoesNotMatchLocallyOpenedDocument(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	telegram := "149.154.167.50"

	total := 0
	for i := 0; i < 40; i++ { // 20 минут keepalive по 1 КБ
		total += len(m.Observe(send(5, telegram, 1_000, t0.Add(time.Duration(i)*30*time.Second))))
	}
	readAt := t0.Add(20*time.Minute + 10*time.Second)
	reads.Add(5, Read{Path: `C:\doc.pdf`, Size: 30_000, At: readAt})
	for i := 1; i <= 20; i++ { // ещё 10 минут keepalive
		total += len(m.Observe(send(5, telegram, 1_000, readAt.Add(time.Duration(i)*30*time.Second-10*time.Second))))
	}
	if total != 0 {
		t.Fatalf("keepalive до чтения засчитан как отправка файла: %d", total)
	}
}

func TestStaleReadIsNotMatchedAfterMaxTransfer(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\stale.pdf`, Size: 20_000, At: t0})

	// Совпадение раньше предела maxTransfer допустимо (объём keepalive с лихвой
	// превышает размер файла). Детерминированно проверяем другое: после
	// maxTransfer чтение кандидатом быть не может, и событий не больше одного.
	total := 0
	for i := 1; i <= 60; i++ { // 30 минут по 5 КБ
		at := t0.Add(time.Duration(i) * 30 * time.Second)
		found := m.Observe(send(5, "149.154.167.50", 5_000, at))
		if len(found) > 0 && at.Sub(t0) > maxTransfer {
			t.Fatalf("совпадение позже maxTransfer: %v", at.Sub(t0))
		}
		total += len(found)
	}
	if total > 1 {
		t.Fatalf("событий %d, ждали не больше 1", total)
	}
}

func TestReadOlderThanMaxTransferNeverMatches(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\stale.pdf`, Size: 1_000, At: t0})
	if got := m.Observe(send(5, "149.154.167.50", 50_000, t0.Add(maxTransfer+time.Second))); len(got) != 0 {
		t.Fatalf("чтение старше maxTransfer: %v", got)
	}
}

func TestReUploadWhileAccumulatorIsAlive(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	file := Read{Path: `C:\f.pdf`, Size: 10_000, At: t0}
	reads.Add(5, file)
	if got := m.Observe(send(5, "162.125.1.14", 10_000, t0)); len(got) != 1 {
		t.Fatalf("первая загрузка: %v", got)
	}
	m.Observe(send(5, "162.125.1.14", 100, t0.Add(30*time.Second)))
	m.Observe(send(5, "162.125.1.14", 100, t0.Add(60*time.Second)))

	file.At = t0.Add(70 * time.Second)
	reads.Add(5, file)
	if got := m.Observe(send(5, "162.125.1.14", 10_000, t0.Add(71*time.Second))); len(got) != 1 {
		t.Fatalf("повторная загрузка при живом накопителе: %v", got)
	}
}

func TestNonPositiveSizeIsIgnored(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\neg.pdf`, Size: -5, At: t0})
	reads.Add(5, Read{Path: `C:\zero.pdf`, Size: 0, At: t0})

	if got := m.Observe(send(5, "162.125.1.14", 5_000, t0)); len(got) != 0 {
		t.Fatalf("%v", got)
	}
}
