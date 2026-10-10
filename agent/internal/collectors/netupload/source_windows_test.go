//go:build windows

package netupload

import (
	"context"
	"errors"
	"net/netip"
	"sync"
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

func TestCollectProps(t *testing.T) {
	get := func(name string) (string, error) {
		if name == "missing" {
			return "", errors.New("нет свойства")
		}
		return "v-" + name, nil
	}
	props := collectProps(get, []string{"a", "missing", "b"})
	if len(props) != 2 || props["a"] != "v-a" || props["b"] != "v-b" {
		t.Fatalf("props: %v", props)
	}
}

func TestWantedFiltersByProviderAndID(t *testing.T) {
	if names, ok := wanted(provFile, idFileCreate); !ok || len(names) != 2 {
		t.Fatalf("Create: %v %v", names, ok)
	}
	// 10 есть у сети, но у файлового провайдера это не нужное событие
	if _, ok := wanted(provFile, 10); ok {
		t.Fatal("id 10 файлового провайдера не нужен")
	}
	if _, ok := wanted(provNet, idTCPv4Send); !ok {
		t.Fatal("отправка TCPv4 нужна")
	}
	if _, ok := wanted(provDNS, idDNSQueryDone); !ok {
		t.Fatal("DNS 3008 нужен")
	}
	if _, ok := wanted(provKind(99), 1); ok {
		t.Fatal("неизвестный провайдер")
	}
}

func TestListenerRoutesByProvider(t *testing.T) {
	var got []Event
	l := &listener{files: newFileTable(), toDOS: fakeDOS, sink: func(e Event) { got = append(got, e) }}
	l.handle(rawEvent{prov: provNet, id: idTCPv4Send, pid: 5, at: testAt,
		props: map[string]interface{}{propNetSize: "10", propNetDest: "1.2.3.4"}})
	// id 10 у файлового провайдера не должен восприниматься как отправка
	l.handle(rawEvent{prov: provFile, id: 10, pid: 5, at: testAt,
		props: map[string]interface{}{propNetSize: "10", propNetDest: "1.2.3.4"}})
	if len(got) != 1 || got[0].Kind != KindSend {
		t.Fatalf("события: %+v", got)
	}
}

// fakeETW — подмена операций ETW: журнал вызовов, управляемое время и сессии,
// которые можно «убить снаружи».
type fakeETW struct {
	mu       sync.Mutex
	calls    []string
	failNext error
	now      time.Time
	sessions []*fakeSession
}

type fakeSession struct {
	ended    chan struct{}
	once     sync.Once
	stopOnce sync.Once
}

func (s *fakeSession) end() { s.once.Do(func() { close(s.ended) }) }

func (f *fakeETW) log(call string) {
	f.mu.Lock()
	f.calls = append(f.calls, call)
	f.mu.Unlock()
}

func (f *fakeETW) callsCopy() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.calls...)
}

func (f *fakeETW) advance(d time.Duration) {
	f.mu.Lock()
	f.now = f.now.Add(d)
	f.mu.Unlock()
}

func (f *fakeETW) last() *fakeSession {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.sessions[len(f.sessions)-1]
}

func (f *fakeETW) hub() *hub {
	return newHub(backend{
		cooldown: 5 * time.Second,
		now: func() time.Time {
			f.mu.Lock()
			defer f.mu.Unlock()
			return f.now
		},
		stopStale: func() error { f.log("stopStale"); return nil },
		launch: func(func(rawEvent)) (*session, error) {
			f.log("launch")
			f.mu.Lock()
			err := f.failNext
			f.failNext = nil
			f.mu.Unlock()
			if err != nil {
				return nil, err
			}
			s := &fakeSession{ended: make(chan struct{})}
			f.mu.Lock()
			f.sessions = append(f.sessions, s)
			f.mu.Unlock()
			return &session{
				wait: func() { <-s.ended },
				stop: func() {
					s.stopOnce.Do(func() { f.log("stop") }) // как в боевой реализации: однократно
					s.end()
				},
			}, nil
		},
	})
}

func newFakeETW() *fakeETW { return &fakeETW{now: testAt} }

func waitListeners(t *testing.T, h *hub, want int) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for {
		h.mu.RLock()
		n := len(h.listeners)
		h.mu.RUnlock()
		if n == want {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("подписчиков %d, ожидалось %d", n, want)
		}
		time.Sleep(time.Millisecond)
	}
}

func countCalls(calls []string, name string) int {
	n := 0
	for _, c := range calls {
		if c == name {
			n++
		}
	}
	return n
}

func TestStaleStopHappensBeforeLaunch(t *testing.T) {
	f := newFakeETW()
	h := f.hub()
	if _, err := h.ensure(); err != nil {
		t.Fatal(err)
	}
	calls := f.callsCopy()
	if len(calls) != 2 || calls[0] != "stopStale" || calls[1] != "launch" {
		t.Fatalf("порядок вызовов: %v", calls)
	}
	// повторный ensure не трогает сессию
	if _, err := h.ensure(); err != nil || len(f.callsCopy()) != 2 {
		t.Fatalf("повторный ensure: %v %v", err, f.callsCopy())
	}
}

func TestRunRetriesAfterFailedStart(t *testing.T) {
	f := newFakeETW()
	f.failNext = errors.New("нет прав")
	h := f.hub()
	if err := runOnHub(context.Background(), h, func(Event) {}); err == nil {
		t.Fatal("ожидалась ошибка запуска")
	}
	// неудачный запуск не включает паузу: сразу можно пробовать снова
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := runOnHub(ctx, h, func(Event) {}); err != nil {
		t.Fatalf("второй запуск: %v", err)
	}
	if got := countCalls(f.callsCopy(), "launch"); got != 2 {
		t.Fatalf("запусков %d, ожидалось 2", got)
	}
	_ = runOnHub(ctx, h, func(Event) {})
	if got := countCalls(f.callsCopy(), "launch"); got != 2 {
		t.Fatalf("сессия запущена повторно: %d", got)
	}
}

func TestRunDeliversAndReturnsPromptlyOnCancel(t *testing.T) {
	f := newFakeETW()
	h := f.hub()
	events := make(chan Event, 4)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- runOnHub(ctx, h, func(e Event) { events <- e }) }()
	waitListeners(t, h, 1)

	h.dispatch(rawEvent{prov: provNet, id: idUDPv4Send, pid: 3, at: testAt,
		props: map[string]interface{}{propNetSize: "7", propNetDest: "8.8.8.8"}})
	select {
	case e := <-events:
		if e.Bytes != 7 || e.PID != 3 {
			t.Fatalf("событие: %+v", e)
		}
	case <-time.After(time.Second):
		t.Fatal("событие не доставлено")
	}

	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("Run: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("Run не вернулся после отмены")
	}
	waitListeners(t, h, 0)
	if countCalls(f.callsCopy(), "stop") != 0 {
		t.Fatal("отмена Run не должна останавливать общую сессию")
	}
}

func TestDeathReturnsErrorAndNextRunRestartsOnce(t *testing.T) {
	f := newFakeETW()
	h := f.hub()
	done := make(chan error, 1)
	go func() { done <- runOnHub(context.Background(), h, func(Event) {}) }()
	waitListeners(t, h, 1)

	f.last().end() // сессию остановили снаружи
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("гибель сессии должна давать ошибку")
		}
	case <-time.After(time.Second):
		t.Fatal("Run не вернулся после гибели сессии")
	}

	f.advance(6 * time.Second)
	ctx, cancel := context.WithCancel(context.Background())
	done2 := make(chan error, 1)
	go func() { done2 <- runOnHub(ctx, h, func(Event) {}) }()
	waitListeners(t, h, 1)
	if got := countCalls(f.callsCopy(), "launch"); got != 2 {
		t.Fatalf("запусков %d, ожидалось ровно 2", got)
	}
	// ещё один Run использует уже запущенную сессию
	ctx2, cancel2 := context.WithCancel(context.Background())
	done3 := make(chan error, 1)
	go func() { done3 <- runOnHub(ctx2, h, func(Event) {}) }()
	waitListeners(t, h, 2)
	if got := countCalls(f.callsCopy(), "launch"); got != 2 {
		t.Fatalf("лишний запуск: %d", got)
	}
	cancel()
	cancel2()
	<-done2
	<-done3
}

func TestRestartCooldown(t *testing.T) {
	f := newFakeETW()
	h := f.hub()
	if _, err := h.ensure(); err != nil {
		t.Fatal(err)
	}
	f.last().end()
	deadline := time.Now().Add(time.Second)
	for {
		h.startMu.Lock()
		dead := h.current == nil
		h.startMu.Unlock()
		if dead {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("гибель не замечена")
		}
		time.Sleep(time.Millisecond)
	}
	f.advance(2 * time.Second)
	if _, err := h.ensure(); err == nil {
		t.Fatal("перезапуск раньше паузы должен отказывать")
	}
	if got := countCalls(f.callsCopy(), "launch"); got != 1 {
		t.Fatalf("запусков %d, ожидался 1", got)
	}
	f.advance(4 * time.Second)
	if _, err := h.ensure(); err != nil {
		t.Fatalf("после паузы перезапуск должен пройти: %v", err)
	}
	if got := countCalls(f.callsCopy(), "launch"); got != 2 {
		t.Fatalf("запусков %d, ожидалось 2", got)
	}
}

func TestShutdownIsIdempotentAndNotADeath(t *testing.T) {
	f := newFakeETW()
	h := f.hub()
	h.shutdown() // до запуска: ничего не делает
	if len(f.callsCopy()) != 0 {
		t.Fatalf("shutdown без сессии вызвал ETW: %v", f.callsCopy())
	}

	done := make(chan error, 1)
	go func() { done <- runOnHub(context.Background(), h, func(Event) {}) }()
	waitListeners(t, h, 1)

	h.shutdown()
	h.shutdown()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("намеренная остановка не должна давать ошибку: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("Run не вернулся после shutdown")
	}
	if got := countCalls(f.callsCopy(), "stop"); got != 1 {
		t.Fatalf("stop вызван %d раз, ожидался 1", got)
	}
}

func TestDispatchSurvivesPanickingListener(t *testing.T) {
	h := newHub(backend{})
	var delivered int
	h.subscribe(func(rawEvent) { panic("битое событие") })
	h.subscribe(func(rawEvent) { delivered++ })
	h.dispatch(rawEvent{prov: provDNS, id: idDNSQueryDone})
	if delivered != 1 {
		t.Fatalf("второй подписчик не получил событие: %d", delivered)
	}
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
