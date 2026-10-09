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

// Reads хранит для каждого процесса последние прочитанные документы.
// Хранение (retention) отделено от запроса: старше retention записи
// выбрасываются при добавлении новых, а Recent отдаёт всё, что не старше
// заданного момента. Память ограничена числом процессов и записей на процесс.
type Reads struct {
	mu        sync.Mutex
	retention time.Duration
	maxPIDs   int
	maxPerPID int
	byPID     map[uint32]*pidReads
}

func NewReads(retention time.Duration, maxPIDs, maxPerPID int) *Reads {
	if maxPerPID < 1 {
		maxPerPID = 1
	}
	return &Reads{retention: retention, maxPIDs: maxPIDs, maxPerPID: maxPerPID, byPID: map[uint32]*pidReads{}}
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
	cutoff := read.At.Add(-r.retention)
	kept := entry.items[:0]
	for _, item := range entry.items {
		if !item.At.Before(cutoff) {
			kept = append(kept, item)
		}
	}
	entry.items = kept
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

// Recent отдаёт чтения процесса, случившиеся не раньше since и не старше
// хранения относительно now.
func (r *Reads) Recent(pid uint32, since, now time.Time) []Read {
	r.mu.Lock()
	defer r.mu.Unlock()
	entry := r.byPID[pid]
	if entry == nil {
		return nil
	}
	var out []Read
	oldest := now.Add(-r.retention)
	for _, read := range entry.items {
		if !read.At.Before(since) && !read.At.Before(oldest) {
			out = append(out, read)
		}
	}
	return out
}

func (r *Reads) Processes() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.byPID)
}
