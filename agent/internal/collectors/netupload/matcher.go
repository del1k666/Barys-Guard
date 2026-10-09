package netupload

import (
	"net/netip"
	"sort"
	"sync"
	"time"
)

const (
	ConfidenceHigh   = "high"
	ConfidenceMedium = "medium"

	pruneAbove = 4096
)

// Send — отправка данных процессом на адрес.
type Send struct {
	PID   uint32
	Addr  netip.Addr
	Bytes uint64
	At    time.Time
}

// Match — найденная отправка файла.
type Match struct {
	PID        uint32
	Read       Read
	Service    Service
	Host       string
	Sent       uint64
	Confidence string
}

type accKey struct {
	pid     uint32
	service string
}

type reportedKey struct {
	pid           uint32
	path, service string
}

type accumulator struct {
	start time.Time
	sent  uint64
	used  uint64
}

// Matcher решает, что процесс отправил именно прочитанный им документ.
// Объём отправки копится по паре «процесс — сервис» и расходуется на файлы по
// убыванию размера; каждый файл засчитывается один раз за окно.
type Matcher struct {
	cfg   Config
	reads *Reads
	res   *Resolver

	mu       sync.Mutex
	acc      map[accKey]*accumulator
	reported map[reportedKey]time.Time
}

func NewMatcher(cfg Config, reads *Reads, res *Resolver) *Matcher {
	if cfg.TolerancePercent < 0 || cfg.TolerancePercent > maxTolerance {
		cfg.TolerancePercent = defaultTolerance
	}
	return &Matcher{
		cfg: cfg, reads: reads, res: res,
		acc: map[accKey]*accumulator{}, reported: map[reportedKey]time.Time{},
	}
}

func (m *Matcher) Observe(send Send) []Match {
	if send.Bytes == 0 {
		return nil
	}
	service, host, ok := m.res.Resolve(send.Addr, send.At)
	if !ok {
		return nil
	}

	m.mu.Lock()
	defer m.mu.Unlock()
	m.pruneLocked(send.At)

	key := accKey{pid: send.PID, service: service.Key}
	acc := m.acc[key]
	if acc == nil || send.At.Sub(acc.start) > m.cfg.Window {
		acc = &accumulator{start: send.At}
		m.acc[key] = acc
	}
	acc.sent += send.Bytes

	candidates := m.reads.Recent(send.PID, send.At)
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].Size != candidates[j].Size {
			return candidates[i].Size > candidates[j].Size
		}
		return candidates[i].Path < candidates[j].Path
	})

	var out []Match
	for _, read := range candidates {
		seenKey := reportedKey{pid: send.PID, path: read.Path, service: service.Key}
		if at, seen := m.reported[seenKey]; seen && send.At.Sub(at) <= m.cfg.Window {
			continue
		}
		size := uint64(read.Size)
		need := size * uint64(100-m.cfg.TolerancePercent) / 100
		remaining := acc.sent - acc.used
		if need == 0 || remaining < need {
			continue
		}
		confidence := ConfidenceMedium
		if remaining >= size {
			confidence = ConfidenceHigh
		}
		spent := size
		if remaining < spent {
			spent = remaining
		}
		acc.used += spent
		m.reported[seenKey] = send.At
		out = append(out, Match{
			PID: send.PID, Read: read, Service: service, Host: host,
			Sent: acc.sent, Confidence: confidence,
		})
	}
	return out
}

// pruneLocked выбрасывает давно закрытые окна, чтобы карты не росли бесконечно.
func (m *Matcher) pruneLocked(now time.Time) {
	if len(m.acc) > pruneAbove {
		for key, acc := range m.acc {
			if now.Sub(acc.start) > 2*m.cfg.Window {
				delete(m.acc, key)
			}
		}
	}
	if len(m.reported) > pruneAbove {
		for key, at := range m.reported {
			if now.Sub(at) > 2*m.cfg.Window {
				delete(m.reported, key)
			}
		}
	}
}
