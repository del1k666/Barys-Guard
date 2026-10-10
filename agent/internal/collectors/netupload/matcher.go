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

	// maxTransfer — самая долгая передача одного прочитанного файла: чтение
	// старше этого к моменту отправки уже не кандидат.
	maxTransfer = 10 * time.Minute
	// readSlack — допуск: отправка, начатая чуть раньше записанного момента
	// чтения (разные источники событий), всё ещё относится к этому чтению.
	readSlack = 2 * time.Second
	// maxSamples — предел ряда отсчётов накопленной отправки в одном accumulator.
	maxSamples = 512
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

type sample struct {
	at  time.Time
	sum uint64 // накопленный объём отправки после этого отсчёта
}

type reportedKey struct {
	path string
	at   time.Time
}

// accumulator — одна непрерывная передача процесса на сервис. Он живёт, пока
// передача не простаивает дольше окна; вместе с ним живёт и список уже
// засчитанных чтений. Ряд отсчётов позволяет для каждого чтения считать только
// то, что отправлено после него.
type accumulator struct {
	start    time.Time
	last     time.Time
	sent     uint64
	used     uint64
	samples  []sample
	reported map[reportedKey]struct{}
}

// addSample дописывает отсчёт; при заполнении выбрасывает каждый второй старый.
func (a *accumulator) addSample(at time.Time) {
	if len(a.samples) >= maxSamples {
		kept := a.samples[:0]
		for index, item := range a.samples {
			if index%2 == 1 {
				kept = append(kept, item)
			}
		}
		a.samples = kept
	}
	a.samples = append(a.samples, sample{at: at, sum: a.sent})
}

// sentBefore — накопленный объём на момент at (последний отсчёт не позже at).
func (a *accumulator) sentBefore(at time.Time) uint64 {
	index := sort.Search(len(a.samples), func(i int) bool { return a.samples[i].at.After(at) })
	if index == 0 {
		return 0
	}
	return a.samples[index-1].sum
}

// Matcher решает, что процесс отправил именно прочитанный им документ.
// Объём отправки копится по паре «процесс — сервис» и расходуется на файлы по
// убыванию размера; каждое чтение засчитывается один раз за непрерывную
// передачу. Для чтения считается только отправленное после него; чтение
// старше maxTransfer или раньше начала передачи более чем на окно не
// рассматривается. Окно — это и допустимая давность чтения до начала
// передачи, и допустимый простой внутри одной передачи.
type Matcher struct {
	cfg   Config
	reads *Reads
	res   *Resolver

	mu  sync.Mutex
	acc map[accKey]*accumulator
}

func NewMatcher(cfg Config, reads *Reads, res *Resolver) *Matcher {
	if cfg.TolerancePercent < 0 || cfg.TolerancePercent > maxTolerance {
		cfg.TolerancePercent = defaultTolerance
	}
	return &Matcher{
		cfg: cfg, reads: reads, res: res,
		acc: map[accKey]*accumulator{},
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
	if acc == nil || send.At.Sub(acc.last) > m.cfg.Window {
		acc = &accumulator{start: send.At, reported: map[reportedKey]struct{}{}}
		m.acc[key] = acc
	}
	if send.At.After(acc.last) {
		acc.last = send.At
	}
	acc.sent += send.Bytes
	acc.addSample(send.At)
	if len(acc.reported) > 256 {
		for seen := range acc.reported {
			if send.At.Sub(seen.at) > maxTransfer {
				delete(acc.reported, seen)
			}
		}
	}

	candidates := m.reads.Recent(send.PID, acc.start.Add(-m.cfg.Window), send.At)
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].Size != candidates[j].Size {
			return candidates[i].Size > candidates[j].Size
		}
		return candidates[i].Path < candidates[j].Path
	})

	var out []Match
	for _, read := range candidates {
		if read.Size <= 0 || send.At.Sub(read.At) > maxTransfer {
			continue
		}
		seenKey := reportedKey{path: read.Path, at: read.At}
		if _, seen := acc.reported[seenKey]; seen {
			continue
		}
		size := uint64(read.Size)
		need := size * uint64(100-m.cfg.TolerancePercent) / 100
		remaining := acc.sent - acc.used
		// Отправленное до чтения к этому файлу не относится.
		if sentSince := acc.sent - acc.sentBefore(read.At.Add(-readSlack)); sentSince < remaining {
			remaining = sentSince
		}
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
		acc.reported[seenKey] = struct{}{}
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
			if now.Sub(acc.last) > 2*m.cfg.Window {
				delete(m.acc, key)
			}
		}
	}
}
