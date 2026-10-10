// Command barysguard-agent — транспортное ядро агента BarysGuard.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/buffer"
	"github.com/barysguard/agent/internal/collectors"
	"github.com/barysguard/agent/internal/collectors/lifecycle"
	"github.com/barysguard/agent/internal/collectors/netupload"
	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/keystore"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/runner"
	"github.com/barysguard/agent/internal/transport"
)

// agentVersion подставляется при сборке: -ldflags "-X main.agentVersion=1.2.3".
var agentVersion = "0.1.0"

// Отдельный код для отзыва нужен супервизору: systemd с
// RestartPreventExitStatus=2 не станет бесконечно поднимать агента,
// чей сертификат отозвали намеренно.
const exitRevoked = 2

func main() {
	var level slog.LevelVar
	if raw := os.Getenv("BG_LOG_LEVEL"); raw != "" {
		_ = level.UnmarshalText([]byte(raw))
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: &level})))

	if len(os.Args) < 2 {
		usage()
		os.Exit(1)
	}

	var err error
	switch os.Args[1] {
	case "enroll":
		err = commandEnroll(os.Args[2:])
	case "run":
		err = commandRun(os.Args[2:])
	case "status":
		err = commandStatus(os.Args[2:])
	default:
		usage()
		os.Exit(1)
	}

	if errors.Is(err, runner.ErrRevoked) {
		slog.Error("обслуживание прекращено сервером", "error", err)
		os.Exit(exitRevoked)
	}
	if err != nil {
		slog.Error("выполнение не удалось", "error", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintf(os.Stderr, `barysguard-agent %s

  enroll  -token <токен> -server <URL> (-ca-file <путь> | -ca-pin <sha256>)
  run     [-data-dir <путь>]
  status  [-data-dir <путь>]
`, agentVersion)
}

func commandEnroll(args []string) error {
	flags := flag.NewFlagSet("enroll", flag.ExitOnError)
	options := enrollOptions{}
	flags.StringVar(&options.dataDir, "data-dir", config.DefaultDir(), "рабочий каталог агента")
	flags.StringVar(&options.serverURL, "server", "", "адрес сервера, например https://dlp.example:8443")
	flags.StringVar(&options.token, "token", "", "одноразовый токен регистрации")
	flags.StringVar(&options.caFile, "ca-file", "", "сертификат CA из дистрибутива")
	flags.StringVar(&options.caPin, "ca-pin", "", "отпечаток SHA-256 сертификата CA")
	flags.BoolVar(&options.force, "force", false, "перезаписать существующую регистрацию")
	if err := flags.Parse(args); err != nil {
		return err
	}

	if options.serverURL == "" || options.token == "" {
		flags.Usage()
		return errors.New("-server и -token обязательны")
	}
	return runEnroll(options)
}

// loadAgent собирает агента из того, что лежит на диске.
func loadAgent(dataDir string) (*runner.Agent, config.Layout, error) {
	layout := config.NewLayout(dataDir)
	guard := platform.New()

	settings, err := config.LoadSettings(layout)
	if err != nil {
		return nil, layout, fmt.Errorf("настройки: %w (агент зарегистрирован?)", err)
	}
	pool, err := keystore.LoadCAPool(layout)
	if err != nil {
		return nil, layout, err
	}
	pair, _, err := keystore.Load(layout, guard)
	if err != nil {
		return nil, layout, err
	}
	client, err := transport.NewMutual(settings.ServerURL, pool, pair)
	if err != nil {
		return nil, layout, err
	}

	state, err := config.LoadState(layout)
	if err != nil {
		return nil, layout, fmt.Errorf("состояние: %w", err)
	}
	buf, reset, err := buffer.OpenAt(layout, guard, buffer.LimitsFromDocument(state.Document))
	if err != nil {
		return nil, layout, fmt.Errorf("буфер событий: %w", err)
	}

	// Копии файлов шифруются тем же ключом, что и буфер событий.
	key, _, err := buffer.LoadOrCreateKey(layout, guard)
	if err != nil {
		buf.Close()
		return nil, layout, fmt.Errorf("ключ копий файлов: %w", err)
	}
	if err := guard.SecureDir(layout.StagingDir()); err != nil {
		buf.Close()
		return nil, layout, fmt.Errorf("каталог копий файлов: %w", err)
	}
	store, err := artifacts.NewStore(layout.StagingDir(), key, time.Now)
	if err != nil {
		buf.Close()
		return nil, layout, fmt.Errorf("каталог копий файлов: %w", err)
	}
	store.SetConfig(artifacts.ConfigFromDocument(state.Document))
	plat := collectors.DefaultPlatform()
	plat.Stager = store

	agent, err := runner.New(runner.Options{
		ServerURL:        settings.ServerURL,
		AgentVersion:     agentVersion,
		Layout:           layout,
		Guard:            guard,
		Client:           client,
		Buffer:           buf,
		Collectors:       []events.Collector{lifecycle.New(agentVersion)},
		CollectorFactory: collectors.NewFactoryFor(plat, layout.Dir),
		Artifacts:        artifacts.NewWorker(store, client),
	})
	if err != nil {
		buf.Close()
		return nil, layout, err
	}
	if reset {
		report, envErr := events.NewEnvelope(events.ChannelAgent, "buffer_reset", events.SeverityHigh, map[string]any{
			"component": "buffer",
			"detail":    "ключ буфера утрачен: прежние неотправленные события потеряны",
		})
		if envErr == nil {
			agent.Emit(report)
		}
	}
	return agent, layout, nil
}

func commandRun(args []string) error {
	flags := flag.NewFlagSet("run", flag.ExitOnError)
	dataDir := flags.String("data-dir", config.DefaultDir(), "рабочий каталог агента")
	if err := flags.Parse(args); err != nil {
		return err
	}

	agent, layout, err := loadAgent(*dataDir)
	if err != nil {
		return err
	}
	defer agent.Close()
	// Сессия ETW сетевого сборщика живёт в ядре дольше процесса: при штатном
	// завершении её нужно остановить, иначе останется сирота.
	defer netupload.Shutdown()

	// Сигнал завершения обязан останавливать цикл, а не обрывать его
	// посреди отправки результата команды.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if _, leaf, err := keystore.Load(layout, platform.New()); err == nil {
		if err := agent.MaybeRenew(ctx, leaf); err != nil {
			slog.Warn("продление сертификата не удалось", "error", err)
		}
	}

	slog.Info("агент запущен", "version", agentVersion, "data_dir", *dataDir)
	return agent.Run(ctx)
}

func commandStatus(args []string) error {
	flags := flag.NewFlagSet("status", flag.ExitOnError)
	dataDir := flags.String("data-dir", config.DefaultDir(), "рабочий каталог агента")
	if err := flags.Parse(args); err != nil {
		return err
	}

	layout := config.NewLayout(*dataDir)
	state, err := config.LoadState(layout)
	if err != nil {
		return err
	}
	if state.AgentID == "" {
		return errors.New("агент не зарегистрирован")
	}

	_, leaf, err := keystore.Load(layout, platform.New())
	if err != nil {
		return err
	}

	fmt.Printf("agent_id:       %s\n", state.AgentID)
	fmt.Printf("config_version: %d\n", state.ConfigVersion)
	fmt.Printf("cert_not_after: %s\n", leaf.NotAfter.Format("2006-01-02 15:04:05 MST"))
	fmt.Printf("renewal_due:    %t\n", keystore.RenewalDue(leaf, time.Now()))
	return nil
}
