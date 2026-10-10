// Package collectors собирает набор сборщиков событий из документа конфигурации.
package collectors

import (
	"os"
	"runtime"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/collectors/netupload"
	"github.com/barysguard/agent/internal/collectors/usb"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/identity"
	"github.com/barysguard/agent/internal/volumes"
)

const defaultPollSeconds = 2

// Platform — всё платформенное, что нужно сборщикам. В тестах подменяется.
type Platform struct {
	Supported    bool
	Provider     volumes.Provider
	Identity     identity.Resolver
	Attributor   filewatch.Attributor
	StartWatcher filewatch.StartWatcher
	Profiles     func() []string
	Stager       artifacts.Stager
	// NetSource — источник событий чтения файлов и сети (ETW); nil — сборщика netupload нет.
	NetSource netupload.Source
	// ProcessInfo определяет процесс по PID для события отправки.
	ProcessInfo func(pid uint32) map[string]any
}

func DefaultPlatform() Platform {
	return Platform{
		Supported:    runtime.GOOS == "windows",
		Provider:     volumes.NewProvider(),
		Identity:     identity.New(),
		Attributor:   filewatch.NewAttributor(),
		StartWatcher: filewatch.DefaultStartWatcher,
		NetSource:    netupload.NewSource(),
		ProcessInfo:  netupload.ProcessInfo,
		Profiles: func() []string {
			return filewatch.ProfileDirs(os.Getenv("SystemDrive") + `\Users`)
		},
	}
}

func enabled(document map[string]any, channel string) bool {
	collectors, _ := document["collectors"].(map[string]any)
	section, _ := collectors[channel].(map[string]any)
	if value, ok := section["enabled"].(bool); ok {
		return value
	}
	return true
}

func pollInterval(document map[string]any) time.Duration {
	collectors, _ := document["collectors"].(map[string]any)
	section, _ := collectors["usb"].(map[string]any)
	if value, ok := section["poll_seconds"].(float64); ok && value >= 1 {
		return time.Duration(value) * time.Second
	}
	return defaultPollSeconds * time.Second
}

// Build строит группу сборщиков по документу. Опрос томов (hub) нужен всем
// сборщикам и запускается, если включён хотя бы один из них.
func Build(document map[string]any, dataDir string, plat Platform) []events.Collector {
	if !plat.Supported {
		return nil
	}
	useUSB := enabled(document, "usb")
	var profiles []string
	if plat.Profiles != nil {
		profiles = plat.Profiles()
	}
	cfg := filewatch.ConfigFromDocument(document, profiles, dataDir)
	useFiles := cfg.Enabled
	netCfg := netupload.ConfigFromDocument(document)
	useNet := plat.NetSource != nil && netCfg.Enabled
	if !useUSB && !useFiles && !useNet {
		return nil
	}

	hub := volumes.NewHub(plat.Provider, pollInterval(document))
	list := []events.Collector{hub}
	if useUSB {
		list = append(list, usb.New(hub, plat.Identity.ConsoleUser))
	}
	if useFiles {
		list = append(list, filewatch.New(filewatch.Deps{
			Config: cfg, Hub: hub, Identity: plat.Identity,
			Attributor: plat.Attributor, StartWatcher: plat.StartWatcher,
			Stager: plat.Stager,
		}))
	}
	if useNet {
		list = append(list, netupload.New(netupload.Deps{
			Config: netCfg, Source: plat.NetSource, Stager: plat.Stager,
			Identity: plat.Identity, ProcessInfo: plat.ProcessInfo, Volumes: hub.Current,
		}))
	}
	return list
}

// NewFactoryFor возвращает фабрику для runner: группа пересобирается по новому
// документу при смене раздела collectors.
func NewFactoryFor(plat Platform, dataDir string) func(map[string]any) []events.Collector {
	return func(document map[string]any) []events.Collector {
		return Build(document, dataDir, plat)
	}
}

func NewFactory(dataDir string) func(map[string]any) []events.Collector {
	return NewFactoryFor(DefaultPlatform(), dataDir)
}
