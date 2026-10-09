//go:build windows

// etwprobe печатает первые события трёх провайдеров ETW, нужных netupload:
// номер события и имена/значения свойств. Запускать от администратора:
//
//	go run ./cmd/etwprobe
//
// Затем открыть сайт, скопировать файл, загрузить документ в облако. По выводу
// сверяются номера и имена из internal/collectors/netupload/ids_windows.go.
// Библиотека etw отдаёт значения свойств строками, поэтому формат (десятичный
// или "0x...") виден прямо в выводе.
package main

import (
	"errors"
	"fmt"
	"os"
	"os/signal"
	"sync"
	"time"

	"github.com/bi-zone/etw"
	"golang.org/x/sys/windows"
)

type provider struct {
	name     string
	guid     string
	keywords uint64
}

func main() {
	providers := []provider{
		{"Kernel-File", "{EDD08927-9CC4-4E65-B970-C2560FB5C289}", 0x10 | 0x20 | 0x80},
		{"Kernel-Network", "{7DD42A49-5329-4832-8DFD-43D979153A88}", 0},
		{"DNS-Client", "{1C95126E-7EEA-49A9-A3FE-A378B03DDB4D}", 0},
	}
	var out sync.Mutex
	var sessions []*etw.Session
	closeAll := func() {
		for _, session := range sessions {
			_ = session.Close()
		}
	}
	for _, p := range providers {
		guid, err := windows.GUIDFromString(p.guid)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			closeAll()
			os.Exit(1)
		}
		opts := []etw.Option{etw.WithName("BarysGuard-Probe-" + p.name)}
		if p.keywords != 0 {
			opts = append(opts, etw.WithMatchKeywords(p.keywords, 0))
		}
		session, err := etw.NewSession(guid, opts...)
		var exists etw.ExistsError
		if errors.As(err, &exists) {
			// Сессия осталась от прошлого запуска пробника: останавливаем и повторяем.
			if killErr := etw.KillSession(exists.SessionName); killErr == nil {
				session, err = etw.NewSession(guid, opts...)
			}
		}
		if err != nil {
			fmt.Fprintf(os.Stderr, "%s: %v\n", p.name, err)
			closeAll()
			os.Exit(1)
		}
		sessions = append(sessions, session)
		name := p.name
		go func() {
			_ = session.Process(func(e *etw.Event) {
				props, _ := e.EventProperties()
				out.Lock()
				defer out.Unlock()
				fmt.Printf("%-14s id=%-5d pid=%-6d %v\n", name, e.Header.ID, e.Header.ProcessID, props)
			})
		}()
	}
	interrupt := make(chan os.Signal, 1)
	signal.Notify(interrupt, os.Interrupt)
	select {
	case <-interrupt:
	case <-time.After(60 * time.Second):
	}
	closeAll()
}
