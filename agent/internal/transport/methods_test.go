package transport_test

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/hostfacts"
	"github.com/barysguard/agent/internal/transport"
)

func TestEnrollSendsTokenAndCSR(t *testing.T) {
	ca := newTestCA(t)
	var received transport.EnrollRequest
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&received)
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(transport.EnrollResponse{
			AgentID:                  "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f",
			CertificatePEM:           "cert",
			CAPEM:                    "ca",
			ConfigVersion:            77,
			HeartbeatIntervalSeconds: 30,
		})
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	response, err := client.Enroll(context.Background(), transport.EnrollRequest{
		Token:  "BG-ENROLL-AAAA",
		CSRPEM: "-----BEGIN CERTIFICATE REQUEST-----",
		Host:   hostfacts.Facts{MachineID: "m-1", Hostname: "ws-1"},
	})
	if err != nil {
		t.Fatalf("Enroll: %v", err)
	}

	if received.Token != "BG-ENROLL-AAAA" {
		t.Fatalf("сервер получил токен %q", received.Token)
	}
	if received.Host.MachineID != "m-1" {
		t.Fatalf("machine_id не доехал: %+v", received.Host)
	}
	if response.ConfigVersion != 77 {
		t.Fatalf("config_version = %d", response.ConfigVersion)
	}
}

func TestHeartbeatRoundTripsCommands(t *testing.T) {
	ca := newTestCA(t)
	var received transport.HeartbeatRequest
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&received)
		json.NewEncoder(w).Encode(transport.HeartbeatResponse{
			ServerTime:               time.Now().UTC(),
			ConfigVersion:            5,
			HeartbeatIntervalSeconds: 45,
			Commands: []transport.QueuedCommand{
				{ID: "c-1", Type: "ping", Payload: map[string]any{}, ExpiresAt: time.Now().Add(time.Hour)},
			},
		})
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	response, err := client.Heartbeat(context.Background(), transport.HeartbeatRequest{
		AgentVersion:  "0.1.0",
		ConfigVersion: 4,
		SentAt:        time.Now().UTC(),
	})
	if err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}

	if received.AgentVersion != "0.1.0" {
		t.Fatalf("agent_version = %q", received.AgentVersion)
	}
	if response.HeartbeatIntervalSeconds != 45 {
		t.Fatalf("интервал = %d", response.HeartbeatIntervalSeconds)
	}
	if len(response.Commands) != 1 || response.Commands[0].Type != "ping" {
		t.Fatalf("команды = %+v", response.Commands)
	}
}

func TestConfigSendsIfNoneMatchAndReportsNotModified(t *testing.T) {
	ca := newTestCA(t)
	var seenETag string
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seenETag = r.Header.Get("If-None-Match")
		w.WriteHeader(http.StatusNotModified)
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	_, notModified, err := client.Config(context.Background(), `"42"`)
	if err != nil {
		// 304 — это не ошибка, и превращаться в неё по дороге не должно.
		t.Fatalf("Config: %v", err)
	}

	if seenETag != `"42"` {
		t.Fatalf("If-None-Match = %q", seenETag)
	}
	if !notModified {
		t.Fatal("ожидался признак «не изменилось»")
	}
}

func TestConfigReturnsDocument(t *testing.T) {
	ca := newTestCA(t)
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("ETag", `"9"`)
		json.NewEncoder(w).Encode(transport.ConfigResponse{
			Version:  9,
			Document: map[string]any{"logging": map[string]any{"level": "debug"}},
		})
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	response, notModified, err := client.Config(context.Background(), "")
	if err != nil {
		t.Fatalf("Config: %v", err)
	}
	if notModified {
		t.Fatal("документ пришёл, признак «не изменилось» неуместен")
	}
	if response.Version != 9 {
		t.Fatalf("версия = %d", response.Version)
	}
}

func TestCommandResultTargetsItsCommand(t *testing.T) {
	ca := newTestCA(t)
	var path string
	var received transport.CommandResultRequest
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		json.NewDecoder(r.Body).Decode(&received)
		w.WriteHeader(http.StatusAccepted)
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	err := client.CommandResult(context.Background(), "c-9", transport.CommandResultRequest{
		Status: transport.StatusDone,
		Result: map[string]any{"pong": true},
	})
	if err != nil {
		t.Fatalf("CommandResult: %v", err)
	}

	if path != "/gateway/v1/commands/c-9/result" {
		t.Fatalf("путь = %q", path)
	}
	if received.Status != "done" {
		t.Fatalf("status = %q", received.Status)
	}
}
