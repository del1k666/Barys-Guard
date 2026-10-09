//go:build windows

package netupload

import (
	"github.com/bi-zone/etw"
	"net/netip"
	"testing"
	"time"
)

var testAt = time.Date(2026, 10, 9, 12, 0, 0, 0, time.UTC)

func fakeDOS(path string) string {
	if path == `\Device\HarddiskVolume3\docs\plan.pdf` {
		return `C:\docs\plan.pdf`
	}
	return ""
}

func TestHandleFilePropsCreateReadClose(t *testing.T) {
	files := newFileTable()
	var got []Event
	sink := func(e Event) { got = append(got, e) }

	handleFileProps(idFileCreate, 10, testAt, map[string]interface{}{
		propFileObject: "0xFFFFA00012345678",
		propFileName:   `\Device\HarddiskVolume3\docs\plan.pdf`,
	}, files, fakeDOS, sink)
	read := map[string]interface{}{propFileObject: "0xFFFFA00012345678"}
	handleFileProps(idFileRead, 77, testAt, read, files, fakeDOS, sink)
	handleFileProps(idFileRead, 77, testAt, read, files, fakeDOS, sink)
	if len(got) != 1 {
		t.Fatalf("ожидалось одно событие чтения, получено %d", len(got))
	}
	if got[0].Kind != KindRead || got[0].PID != 77 || got[0].Path != `C:\docs\plan.pdf` || !got[0].At.Equal(testAt) {
		t.Fatalf("событие: %+v", got[0])
	}

	handleFileProps(idFileClose, 77, testAt, read, files, fakeDOS, sink)
	handleFileProps(idFileRead, 77, testAt, read, files, fakeDOS, sink)
	if len(got) != 1 {
		t.Fatal("после закрытия чтение не должно давать событий")
	}
}

func TestHandleFilePropsIgnoresUnknownPathAndBadProps(t *testing.T) {
	files := newFileTable()
	sink := func(Event) { t.Fatal("событий быть не должно") }
	// путь не переводится в DOS: объект не запоминается
	handleFileProps(idFileCreate, 1, testAt, map[string]interface{}{
		propFileObject: "5", propFileName: `\Device\Nope\x`,
	}, files, fakeDOS, sink)
	handleFileProps(idFileRead, 1, testAt, map[string]interface{}{propFileObject: "5"}, files, fakeDOS, sink)
	// нет FileObject / не строка в имени
	handleFileProps(idFileCreate, 1, testAt, map[string]interface{}{propFileName: "x"}, files, fakeDOS, sink)
	handleFileProps(idFileCreate, 1, testAt, map[string]interface{}{propFileObject: "5", propFileName: 7}, files, fakeDOS, sink)
	handleFileProps(idFileRead, 1, testAt, map[string]interface{}{}, files, fakeDOS, sink)
	handleFileProps(999, 1, testAt, map[string]interface{}{propFileObject: "5"}, files, fakeDOS, sink)
	if files.size() != 0 {
		t.Fatalf("таблица должна быть пустой, размер %d", files.size())
	}
}

func TestHandleNetworkProps(t *testing.T) {
	var got []Event
	sink := func(e Event) { got = append(got, e) }

	handleNetworkProps(idTCPv4Send, 4, testAt, map[string]interface{}{
		propNetPID: "1234", propNetSize: "65536", propNetDest: "142.250.1.1",
	}, sink)
	if len(got) != 1 {
		t.Fatalf("событий: %d", len(got))
	}
	e := got[0]
	if e.Kind != KindSend || e.PID != 1234 || e.Bytes != 65536 || e.Addr != netip.MustParseAddr("142.250.1.1") {
		t.Fatalf("событие: %+v", e)
	}

	// PID из заголовка, если в свойствах его нет
	handleNetworkProps(idUDPv6Send, 99, testAt, map[string]interface{}{
		propNetSize: "10", propNetDest: "2001:db8::1",
	}, sink)
	if len(got) != 2 || got[1].PID != 99 {
		t.Fatalf("PID из заголовка: %+v", got)
	}

	// отбрасывается: чужой номер, нулевой размер, плохой адрес, нет размера
	handleNetworkProps(1, 1, testAt, map[string]interface{}{propNetSize: "5", propNetDest: "1.1.1.1"}, sink)
	handleNetworkProps(idTCPv4Send, 1, testAt, map[string]interface{}{propNetSize: "0", propNetDest: "1.1.1.1"}, sink)
	handleNetworkProps(idTCPv4Send, 1, testAt, map[string]interface{}{propNetSize: "5", propNetDest: "zzz"}, sink)
	handleNetworkProps(idTCPv4Send, 1, testAt, map[string]interface{}{propNetDest: "1.1.1.1"}, sink)
	if len(got) != 2 {
		t.Fatalf("лишние события: %d", len(got))
	}
}

func TestHandleDNSProps(t *testing.T) {
	var got []Event
	sink := func(e Event) { got = append(got, e) }

	handleDNSProps(idDNSQueryDone, testAt, map[string]interface{}{
		propDNSName:    "drive.google.com",
		propDNSResults: "type:  5 www3.l.google.com;142.250.1.1;",
	}, sink)
	if len(got) != 1 {
		t.Fatalf("событий: %d", len(got))
	}
	e := got[0]
	if e.Kind != KindDNS || len(e.Names) != 2 || len(e.Addrs) != 1 || e.TTL != 5*time.Minute {
		t.Fatalf("событие: %+v", e)
	}

	handleDNSProps(idDNSQueryDone, testAt, map[string]interface{}{propDNSName: "a.com", propDNSResults: ""}, sink)
	handleDNSProps(idDNSQueryDone, testAt, map[string]interface{}{}, sink)
	handleDNSProps(1, testAt, map[string]interface{}{propDNSName: "a.com", propDNSResults: "1.1.1.1;"}, sink)
	if len(got) != 1 {
		t.Fatalf("лишние события: %d", len(got))
	}
}

func TestSafeRecoversPanic(t *testing.T) {
	callback := safe(func(*etw.Event) { panic("битое событие") })
	callback(nil) // паника не должна выйти наружу
}

func TestIsNetworkSend(t *testing.T) {
	for _, id := range []uint16{idTCPv4Send, idTCPv6Send, idUDPv4Send, idUDPv6Send} {
		if !isNetworkSend(id) {
			t.Errorf("id %d должен считаться отправкой", id)
		}
	}
	if isNetworkSend(idFileRead) {
		t.Error("чтение файла не отправка")
	}
}
