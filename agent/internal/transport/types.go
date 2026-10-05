// Package transport реализует протокол агента. Это единственный пакет,
// знающий про HTTP: расписание и повторы живут в runner.
package transport

import (
	"time"

	"github.com/barysguard/agent/internal/hostfacts"
)

// Значения поля status в CommandResultRequest. Контракт допускает только их.
const (
	StatusDone   = "done"
	StatusFailed = "failed"
)

type EnrollRequest struct {
	Token  string          `json:"token"`
	CSRPEM string          `json:"csr_pem"`
	Host   hostfacts.Facts `json:"host"`
}

type EnrollResponse struct {
	AgentID                  string `json:"agent_id"`
	CertificatePEM           string `json:"certificate_pem"`
	CAPEM                    string `json:"ca_pem"`
	ConfigVersion            int    `json:"config_version"`
	HeartbeatIntervalSeconds int    `json:"heartbeat_interval_seconds"`
}

type RenewRequest struct {
	CSRPEM string `json:"csr_pem"`
}

type RenewResponse struct {
	CertificatePEM string    `json:"certificate_pem"`
	CAPEM          string    `json:"ca_pem"`
	NotAfter       time.Time `json:"not_after"`
}

// HeartbeatRequest сообщает серверу состояние offline-буфера событий.
type HeartbeatRequest struct {
	AgentVersion   string    `json:"agent_version"`
	ConfigVersion  int       `json:"config_version"`
	SentAt         time.Time `json:"sent_at"`
	BufferedEvents int       `json:"buffered_events"`
	BufferBytes    int       `json:"buffer_bytes"`
}

type QueuedCommand struct {
	ID        string         `json:"id"`
	Type      string         `json:"type"`
	Payload   map[string]any `json:"payload"`
	ExpiresAt time.Time      `json:"expires_at"`
}

type HeartbeatResponse struct {
	ServerTime               time.Time       `json:"server_time"`
	ConfigVersion            int             `json:"config_version"`
	HeartbeatIntervalSeconds int             `json:"heartbeat_interval_seconds"`
	Commands                 []QueuedCommand `json:"commands"`
}

type ConfigResponse struct {
	Version  int            `json:"version"`
	Document map[string]any `json:"document"`
}

type CommandResultRequest struct {
	Status string         `json:"status"`
	Result map[string]any `json:"result"`
}

// RejectedEvent — строка пакета, которую сервер не принял. Повторять её
// бессмысленно: такое событие не станет верным от повторной отправки.
type RejectedEvent struct {
	Line   int    `json:"line"`
	Reason string `json:"reason"`
}

type EventsResult struct {
	Accepted   int             `json:"accepted"`
	Duplicates int             `json:"duplicates"`
	Rejected   []RejectedEvent `json:"rejected"`
}
