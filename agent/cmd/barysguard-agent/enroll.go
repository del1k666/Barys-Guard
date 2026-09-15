package main

import (
	"context"
	"errors"
	"fmt"
	"os"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/hostfacts"
	"github.com/barysguard/agent/internal/keystore"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/transport"
)

type enrollOptions struct {
	dataDir   string
	serverURL string
	token     string
	caFile    string
	caPin     string
	force     bool
}

// trustAnchor добывает CA, которому агент будет доверять.
//
// Молчаливого доверия к тому, что пришло по сети, здесь нет: либо файл
// из дистрибутива, либо отпечаток, с которым сверяется загруженное.
func trustAnchor(ctx context.Context, options enrollOptions) ([]byte, error) {
	if options.caFile != "" {
		return os.ReadFile(options.caFile)
	}
	if options.caPin != "" {
		return transport.FetchCA(ctx, options.serverURL, options.caPin)
	}
	return nil, errors.New("нужен --ca-file из дистрибутива либо --ca-pin с отпечатком CA")
}

func runEnroll(options enrollOptions) error {
	ctx := context.Background()
	layout := config.NewLayout(options.dataDir)
	guard := platform.New()

	if !options.force {
		if _, err := os.Stat(layout.CertPath()); err == nil {
			return fmt.Errorf("агент уже зарегистрирован: %s существует (--force перезапишет)", layout.CertPath())
		}
	}

	caPEM, err := trustAnchor(ctx, options)
	if err != nil {
		return fmt.Errorf("якорь доверия: %w", err)
	}
	if err := keystore.SaveCA(layout, guard, caPEM); err != nil {
		return err
	}
	pool, err := keystore.LoadCAPool(layout)
	if err != nil {
		return err
	}

	key, err := keystore.GenerateKey()
	if err != nil {
		return err
	}
	csrPEM, err := keystore.CreateCSR(key)
	if err != nil {
		return err
	}
	facts, err := hostfacts.Collect(guard, agentVersion)
	if err != nil {
		return err
	}

	client, err := transport.NewBootstrap(options.serverURL, pool)
	if err != nil {
		return err
	}
	response, err := client.Enroll(ctx, transport.EnrollRequest{
		Token:  options.token,
		CSRPEM: csrPEM,
		Host:   facts,
	})
	if err != nil {
		return fmt.Errorf("регистрация: %w", err)
	}

	keyPEM, err := keystore.EncodeKey(key)
	if err != nil {
		return err
	}
	if err := keystore.Save(layout, guard, keyPEM, []byte(response.CertificatePEM)); err != nil {
		return err
	}
	// CA из ответа заменяет бутстрапный: сервер мог отдать полную цепочку.
	if response.CAPEM != "" {
		if err := keystore.SaveCA(layout, guard, []byte(response.CAPEM)); err != nil {
			return err
		}
	}

	if err := config.SaveSettings(layout, config.Settings{
		ServerURL: options.serverURL,
		LogLevel:  "info",
	}, guard); err != nil {
		return err
	}
	if err := config.SaveState(layout, config.State{
		AgentID:       response.AgentID,
		ConfigVersion: response.ConfigVersion,
	}, guard); err != nil {
		return err
	}

	fmt.Printf("агент зарегистрирован: %s\n", response.AgentID)
	return nil
}
