//go:build windows

package netupload

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/bi-zone/etw"
	"golang.org/x/sys/windows"
)

type etwSource struct{}

// NewSource возвращает источник на основе сессий ETW.
func NewSource() Source { return etwSource{} }

type feed struct {
	name     string
	guid     windows.GUID
	keywords uint64
	handle   etw.EventCallback
}

// openSession создаёт сессию ETW. Если сессия с таким именем осталась от
// аварийно завершившегося запуска (ExistsError), она один раз принудительно
// останавливается и создание повторяется — иначе источник был бы недоступен
// до перезагрузки.
func openSession(f feed) (*etw.Session, error) {
	options := []etw.Option{etw.WithName(f.name)}
	if f.keywords != 0 {
		options = append(options, etw.WithMatchKeywords(f.keywords, 0))
	}
	session, err := etw.NewSession(f.guid, options...)
	var exists etw.ExistsError
	if errors.As(err, &exists) {
		if killErr := etw.KillSession(exists.SessionName); killErr != nil {
			return nil, fmt.Errorf("%w (остановить старую сессию не удалось: %v)", err, killErr)
		}
		session, err = etw.NewSession(f.guid, options...)
	}
	return session, err
}

// safe оборачивает обработчик событий: паника на неожиданном событии не должна
// ронять агента (обработчик вызывается из потока ETW).
func safe(handle func(*etw.Event)) etw.EventCallback {
	return func(e *etw.Event) {
		defer func() { _ = recover() }()
		handle(e)
	}
}

// Run открывает три сессии ETW (файлы, сеть, DNS) и работает до отмены контекста.
// После отмены все сессии закрываются и Run возвращается без ожидания событий:
// группа сборщиков перезапускается при смене конфигурации и ждёт Run.
func (etwSource) Run(ctx context.Context, sink func(Event)) error {
	files := newFileTable()
	dos := newDosMap()
	feeds := []feed{
		{"BarysGuard-NetUpload-File", guidKernelFile, keywordsKernelFile, safe(func(e *etw.Event) {
			props, err := e.EventProperties()
			if err != nil {
				return
			}
			handleFileProps(e.Header.ID, e.Header.ProcessID, e.Header.TimeStamp, props, files, dos.toDOS, sink)
		})},
		{"BarysGuard-NetUpload-Net", guidKernelNetwork, 0, safe(func(e *etw.Event) {
			if !isNetworkSend(e.Header.ID) {
				return
			}
			props, err := e.EventProperties()
			if err != nil {
				return
			}
			handleNetworkProps(e.Header.ID, e.Header.ProcessID, e.Header.TimeStamp, props, sink)
		})},
		{"BarysGuard-NetUpload-DNS", guidDNSClient, 0, safe(func(e *etw.Event) {
			if e.Header.ID != idDNSQueryDone {
				return
			}
			props, err := e.EventProperties()
			if err != nil {
				return
			}
			handleDNSProps(e.Header.ID, e.Header.TimeStamp, props, sink)
		})},
	}

	var sessions []*etw.Session
	closeAll := func() {
		for _, session := range sessions {
			_ = session.Close()
		}
	}
	for _, f := range feeds {
		session, err := openSession(f)
		if err != nil {
			closeAll()
			return fmt.Errorf("сессия ETW %s: %w", f.name, err)
		}
		sessions = append(sessions, session)
	}

	results := make(chan error, len(sessions))
	for index, session := range sessions {
		handle := feeds[index].handle
		go func() { results <- session.Process(handle) }()
	}

	select {
	case <-ctx.Done():
		closeAll()
		for range sessions {
			<-results
		}
		return nil
	case err := <-results:
		closeAll()
		for i := 1; i < len(sessions); i++ {
			<-results
		}
		if err == nil {
			err = errors.New("сессия ETW завершилась без ошибки")
		}
		return err
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
