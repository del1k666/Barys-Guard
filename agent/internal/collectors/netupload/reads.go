package netupload

import (
	"sync"
	"time"
)

// Read — документ, который процесс прочитал.
type Read struct {
	Path string
	Size int64
	At   time.Time
}

type pidReads struct {
	items []Read
	last  time.Time
}

// Reads хранит для каждого процесса короткое окно последних прочитанных
// документов. Память ограничена числом процессов и записей на процесс.
type Reads struct {
	mu        sync.Mutex
	window    time.Duration
	maxPIDs   int
	maxPerPID int
	byPID     map[uint32]*pidReads
}

func NewReads(window time.Duration, maxPIDs, maxPerPID int) *Reads {
	return &Reads{window: window, maxPIDs: maxPIDs, maxPerPID: maxPerPID, byPID: map[uint32]*pidReads{}}
}

func (r *Reads) Add(pid uint32, read Read) {
	r.mu.Lock()
	defer r.mu.Unlock()
	entry := r.byPID[pid]
	if entry == nil {
		if len(r.byPID) >= r.maxPIDs {
			r.evictOldestLocked()
		}
		entry = &pidReads{}
		r.byPID[pid] = entry
	}
	entry.last = read.At
	for index := range entry.items {
		if entry.items[index].Path == read.Path {
			entry.items[index] = read
			return
		}
	}
	if len(entry.items) >= r.maxPerPID {
		entry.items = entry.items[1:]
	}
	entry.items = append(entry.items, read)
}

func (r *Reads) evictOldestLocked() {
	var oldest uint32
	var oldestAt time.Time
	first := true
	for pid, entry := range r.byPID {
		if first || entry.last.Before(oldestAt) {
			oldest, oldestAt, first = pid, entry.last, false
		}
	}
	if !first {
		delete(r.byPID, oldest)
	}
}

// Recent отдаёт чтения процесса не старше окна.
func (r *Reads) Recent(pid uint32, now time.Time) []Read {
	r.mu.Lock()
	defer r.mu.Unlock()
	entry := r.byPID[pid]
	if entry == nil {
		return nil
	}
	fresh := entry.items[:0]
	for _, read := range entry.items {
		if now.Sub(read.At) <= r.window {
			fresh = append(fresh, read)
		}
	}
	entry.items = fresh
	return append([]Read(nil), fresh...)
}

func (r *Reads) Processes() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.byPID)
}
