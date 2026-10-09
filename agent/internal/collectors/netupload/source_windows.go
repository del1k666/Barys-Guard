//go:build windows

package netupload

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/0xrawsec/golang-etw/etw"
)

// sessionName — имя единственной сессии ETW агента.
const sessionName = "BarysGuard-NetUpload"

type etwSource struct{}

// NewSource возвращает источник на основе общей для процесса сессии ETW.
func NewSource() Source { return etwSource{} }

// provKind — какой из трёх провайдеров породил событие.
type provKind int

const (
	provFile provKind = iota + 1
	provNet
	provDNS
)

// rawEvent — событие ETW с уже разобранными нужными свойствами.
type rawEvent struct {
	prov  provKind
	id    uint16
	pid   uint32
	at    time.Time
	props map[string]interface{}
}

// wantedProps — какие события нужны и какие свойства у них читать. Всё
// остальное отбрасывается до разбора свойств.
var wantedProps = map[provKind]map[uint16][]string{
	provFile: {
		idFileCreate: {propFileObject, propFileName},
		idFileRead:   {propFileObject},
		idFileClose:  {propFileObject},
	},
	provNet: {
		idTCPv4Send: {propNetPID, propNetSize, propNetDest},
		idTCPv6Send: {propNetPID, propNetSize, propNetDest},
		idUDPv4Send: {propNetPID, propNetSize, propNetDest},
		idUDPv6Send: {propNetPID, propNetSize, propNetDest},
	},
	provDNS: {
		idDNSQueryDone: {propDNSName, propDNSResults},
	},
}

var (
	guidFile = etw.MustParseGUIDFromString(guidKernelFile)
	guidNet  = etw.MustParseGUIDFromString(guidKernelNetwork)
	guidDNS  = etw.MustParseGUIDFromString(guidDNSClient)
)

// classify определяет провайдера по заголовку записи события.
func classify(record *etw.EventRecord) (provKind, bool) {
	provider := &record.EventHeader.ProviderId
	switch {
	case provider.Equals(guidFile):
		return provFile, true
	case provider.Equals(guidNet):
		return provNet, true
	case provider.Equals(guidDNS):
		return provDNS, true
	}
	return 0, false
}

// wanted сообщает, нужно ли событие (проверка по заголовку, без разбора свойств).
func wanted(prov provKind, id uint16) ([]string, bool) {
	names, ok := wantedProps[prov][id]
	return names, ok
}

// collectProps читает именованные свойства через get; нечитаемые пропускаются.
func collectProps(get func(string) (string, error), names []string) map[string]interface{} {
	props := make(map[string]interface{}, len(names))
	for _, name := range names {
		if value, err := get(name); err == nil {
			props[name] = value
		}
	}
	return props
}

// hub — общий для процесса приёмник ETW. Сессия и потребитель запускаются один
// раз: каждый запуск потребителя навсегда занимает слоты обратных вызовов
// syscall (их около 1024 на процесс), поэтому перезапуск группы сборщиков
// не должен создавать новые. Каждый Source.Run лишь подписывается на события.
type hub struct {
	startMu sync.Mutex
	started bool
	start   func(dispatch func(rawEvent)) error

	mu        sync.RWMutex
	next      int
	listeners map[int]func(rawEvent)
}

func newHub(start func(dispatch func(rawEvent)) error) *hub {
	return &hub{start: start, listeners: map[int]func(rawEvent){}}
}

// ensure запускает сессию, если она ещё не запущена. После неудачи следующий
// вызов пробует снова.
func (h *hub) ensure() error {
	h.startMu.Lock()
	defer h.startMu.Unlock()
	if h.started {
		return nil
	}
	if err := h.start(h.dispatch); err != nil {
		return err
	}
	h.started = true
	return nil
}

func (h *hub) subscribe(listener func(rawEvent)) (unsubscribe func()) {
	h.mu.Lock()
	h.next++
	id := h.next
	h.listeners[id] = listener
	h.mu.Unlock()
	return func() {
		h.mu.Lock()
		delete(h.listeners, id)
		h.mu.Unlock()
	}
}

// dispatch раздаёт событие подписчикам. Паника подписчика не выходит наружу:
// вызов идёт из потока ETW и не должен ронять агента.
func (h *hub) dispatch(event rawEvent) {
	h.mu.RLock()
	snapshot := make([]func(rawEvent), 0, len(h.listeners))
	for _, listener := range h.listeners {
		snapshot = append(snapshot, listener)
	}
	h.mu.RUnlock()
	for _, listener := range snapshot {
		func() {
			defer func() { _ = recover() }()
			listener(event)
		}()
	}
}

// shared — единственный приёмник процесса.
var shared = newHub(startETW)

// startETW создаёт сессию с тремя провайдерами и запускает потребитель. Сессия
// остаётся работать до конца процесса, потребитель никогда не останавливается.
// Устаревшую сессию с тем же именем (после аварийного завершения) библиотека
// останавливает и создаёт заново сама.
func startETW(dispatch func(rawEvent)) error {
	session := etw.NewRealTimeSession(sessionName)
	providers := []etw.Provider{
		{GUID: guidKernelFile, Name: "Microsoft-Windows-Kernel-File", EnableLevel: 0xFF,
			MatchAnyKeyword: keywordsKernelFile,
			Filter:          []uint16{idFileCreate, idFileClose, idFileRead}},
		{GUID: guidKernelNetwork, Name: "Microsoft-Windows-Kernel-Network", EnableLevel: 0xFF,
			Filter: []uint16{idTCPv4Send, idTCPv6Send, idUDPv4Send, idUDPv6Send}},
		{GUID: guidDNSClient, Name: "Microsoft-Windows-DNS-Client", EnableLevel: 0xFF,
			Filter: []uint16{idDNSQueryDone}},
	}
	for _, provider := range providers {
		if err := session.EnableProvider(provider); err != nil {
			_ = session.Stop()
			return fmt.Errorf("сессия ETW, провайдер %s: %w", provider.Name, err)
		}
	}

	consumer := etw.NewRealTimeConsumer(context.Background()).FromSessions(session)
	// Собственные обратные вызовы: стандартные кладут события в канал, который
	// никто не читает, и блокируются. Полный разбор события отключён
	// (EventCallback == nil), свойства читаются точечно в PreparedCallback.
	consumer.EventRecordHelperCallback = nil
	consumer.EventCallback = nil
	consumer.EventRecordCallback = func(record *etw.EventRecord) (keep bool) {
		defer func() {
			if recover() != nil {
				keep = false
			}
		}()
		prov, ok := classify(record)
		if !ok {
			return false
		}
		_, ok = wanted(prov, record.EventHeader.EventDescriptor.Id)
		return ok
	}
	consumer.PreparedCallback = func(helper *etw.EventRecordHelper) (err error) {
		defer func() { _ = recover() }()
		prov, ok := classify(helper.EventRec)
		if !ok {
			return nil
		}
		id := helper.EventRec.EventHeader.EventDescriptor.Id
		names, ok := wanted(prov, id)
		if !ok {
			return nil
		}
		dispatch(rawEvent{
			prov:  prov,
			id:    id,
			pid:   helper.EventRec.EventHeader.ProcessId,
			at:    helper.EventRec.EventHeader.UTCTimeStamp(),
			props: collectProps(helper.GetPropertyString, names),
		})
		return nil
	}
	if err := consumer.Start(); err != nil {
		_ = consumer.Stop()
		_ = session.Stop()
		return fmt.Errorf("потребитель ETW: %w", err)
	}
	return nil
}

// Run подписывает приёмник на общий поток событий ETW и ждёт отмены контекста.
// Если сессию не удалось запустить (нет прав), возвращается ошибка, а следующий
// Run попробует снова. Общий потребитель не останавливается.
func (etwSource) Run(ctx context.Context, sink func(Event)) error {
	return runOnHub(ctx, shared, sink)
}

func runOnHub(ctx context.Context, h *hub, sink func(Event)) error {
	if err := h.ensure(); err != nil {
		return err
	}
	l := &listener{files: newFileTable(), toDOS: newDosMap().toDOS, sink: sink}
	unsubscribe := h.subscribe(l.handle)
	defer unsubscribe()
	<-ctx.Done()
	return nil
}

// listener превращает события ETW одного запуска Run в события источника.
type listener struct {
	files *fileTable
	toDOS func(string) string
	sink  func(Event)
}

func (l *listener) handle(event rawEvent) {
	switch event.prov {
	case provFile:
		handleFileProps(event.id, event.pid, event.at, event.props, l.files, l.toDOS, l.sink)
	case provNet:
		handleNetworkProps(event.id, event.pid, event.at, event.props, l.sink)
	case provDNS:
		handleDNSProps(event.id, event.at, event.props, l.sink)
	}
}

// handleFileProps обрабатывает событие Kernel-File по уже разобранным свойствам.
func handleFileProps(id uint16, pid uint32, at time.Time, props map[string]interface{},
	files *fileTable, toDOS func(string) string, sink func(Event)) {
	switch id {
	case idFileCreate:
		object, ok := asUint(props[propFileObject])
		name, _ := props[propFileName].(string)
		if !ok || name == "" {
			return
		}
		if path := toDOS(name); path != "" {
			files.set(object, path)
		}
	case idFileRead:
		object, ok := asUint(props[propFileObject])
		if !ok {
			return
		}
		if path, first := files.firstRead(object); first {
			sink(Event{Kind: KindRead, PID: pid, Path: path, At: at})
		}
	case idFileClose:
		if object, ok := asUint(props[propFileObject]); ok {
			files.drop(object)
		}
	}
}

func isNetworkSend(id uint16) bool {
	switch id {
	case idTCPv4Send, idTCPv6Send, idUDPv4Send, idUDPv6Send:
		return true
	}
	return false
}

// handleNetworkProps обрабатывает событие отправки Kernel-Network.
func handleNetworkProps(id uint16, headerPID uint32, at time.Time, props map[string]interface{}, sink func(Event)) {
	if !isNetworkSend(id) {
		return
	}
	size, ok := asUint(props[propNetSize])
	dest, destOK := asAddr(props[propNetDest])
	if !ok || !destOK || size == 0 {
		return
	}
	pid := headerPID
	if owner, ok := asUint(props[propNetPID]); ok && owner != 0 {
		pid = uint32(owner)
	}
	sink(Event{Kind: KindSend, PID: pid, Addr: dest, Bytes: size, At: at})
}

// handleDNSProps обрабатывает завершённый запрос DNS-клиента.
func handleDNSProps(id uint16, at time.Time, props map[string]interface{}, sink func(Event)) {
	if id != idDNSQueryDone {
		return
	}
	name, _ := props[propDNSName].(string)
	results, _ := props[propDNSResults].(string)
	names, addrs := ParseDNSResults(name, results)
	if len(names) == 0 || len(addrs) == 0 {
		return
	}
	sink(Event{Kind: KindDNS, Names: names, Addrs: addrs, TTL: 5 * time.Minute, At: at})
}
