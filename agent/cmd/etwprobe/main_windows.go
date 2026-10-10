//go:build windows

// etwprobe печатает события трёх провайдеров ETW, нужных netupload: номер
// события и имена/значения свойств. Запускать от администратора:
//
//	go run ./cmd/etwprobe
//
// Затем открыть сайт, скопировать файл, загрузить документ в облако. По выводу
// сверяются номера и имена из internal/collectors/netupload/ids_windows.go.
// Значения свойств библиотека отдаёт строками, поэтому формат (десятичный или
// "0x...") виден прямо в выводе. Фильтра по номерам событий нет: видно всё,
// что провайдер шлёт при выбранных ключевых словах. Через 60 секунд или по
// Ctrl+C сессия останавливается.
package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"sync"
	"time"

	"github.com/0xrawsec/golang-etw/etw"
)

func main() {
	session := etw.NewRealTimeSession("BarysGuard-Probe")
	providers := []etw.Provider{
		{GUID: "{EDD08927-9CC4-4E65-B970-C2560FB5C289}", Name: "Kernel-File", EnableLevel: 0xFF,
			MatchAnyKeyword: 0x10 | 0x20 | 0x80 | 0x100},
		{GUID: "{7DD42A49-5329-4832-8DFD-43D979153A88}", Name: "Kernel-Network", EnableLevel: 0xFF},
		{GUID: "{1C95126E-7EEA-49A9-A3FE-A378B03DDB4D}", Name: "DNS-Client", EnableLevel: 0xFF},
	}
	for _, provider := range providers {
		if err := session.EnableProvider(provider); err != nil {
			fmt.Fprintf(os.Stderr, "%s: %v\n", provider.Name, err)
			if stopErr := session.Stop(); stopErr != nil {
				fmt.Fprintf(os.Stderr, "остановка сессии: %v\n", stopErr)
			}
			os.Exit(1)
		}
	}

	var out sync.Mutex
	consumer := etw.NewRealTimeConsumer(context.Background()).FromSessions(session)
	consumer.EventRecordHelperCallback = nil // без фильтра по номерам
	consumer.EventCallback = func(e *etw.Event) error {
		out.Lock()
		defer out.Unlock()
		fmt.Printf("%-24s id=%-5d pid=%-6d %v\n",
			e.System.Provider.Name, e.System.EventID, e.System.Execution.ProcessID, e.EventData)
		return nil
	}
	if err := consumer.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "потребитель: %v\n", err)
		_ = consumer.Stop()
		_ = session.Stop()
		os.Exit(1)
	}

	interrupt := make(chan os.Signal, 1)
	signal.Notify(interrupt, os.Interrupt)
	select {
	case <-interrupt:
	case <-time.After(60 * time.Second):
	}
	if err := consumer.Stop(); err != nil {
		fmt.Fprintf(os.Stderr, "остановка потребителя: %v\n", err)
	}
	if err := session.Stop(); err != nil {
		fmt.Fprintf(os.Stderr, "остановка сессии: %v\n", err)
	}
}
