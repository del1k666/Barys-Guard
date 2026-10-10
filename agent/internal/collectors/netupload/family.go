package netupload

import (
	"strings"
	"sync"
	"time"
)

const (
	familyDepth = 8
	familyTTL   = 5 * time.Minute
	familyMax   = 4096
)

// familyRoot поднимается от pid по родителям, пока образ предка совпадает с
// образом процесса, и возвращает самого верхнего такого предка. У Chromium и
// Electron файл читает главный процесс, а в сеть пишет дочерний сетевой
// (тот же brave.exe/chrome.exe), поэтому «процесс» для сопоставления — это
// семья одного приложения.
func familyRoot(pid uint32, parentOf func(uint32) (uint32, bool), imageOf func(uint32) string) uint32 {
	image := strings.ToLower(imageOf(pid))
	if image == "" {
		return pid
	}
	root := pid
	for range familyDepth {
		parent, ok := parentOf(root)
		if !ok || parent == 0 || parent == root {
			break
		}
		if strings.ToLower(imageOf(parent)) != image {
			break
		}
		root = parent
	}
	return root
}

// familyCache запоминает семью процесса: PID переиспользуются, поэтому записи
// живут недолго.
type familyCache struct {
	mu      sync.Mutex
	now     func() time.Time
	resolve func(uint32) uint32
	entries map[uint32]familyEntry
}

type familyEntry struct {
	root    uint32
	expires time.Time
}

func newFamilyCache(resolve func(uint32) uint32, now func() time.Time) *familyCache {
	return &familyCache{now: now, resolve: resolve, entries: map[uint32]familyEntry{}}
}

func (c *familyCache) Root(pid uint32) uint32 {
	now := c.now()
	c.mu.Lock()
	entry, ok := c.entries[pid]
	c.mu.Unlock()
	if ok && now.Before(entry.expires) {
		return entry.root
	}
	root := c.resolve(pid)
	c.mu.Lock()
	if len(c.entries) >= familyMax {
		c.entries = map[uint32]familyEntry{}
	}
	c.entries[pid] = familyEntry{root: root, expires: now.Add(familyTTL)}
	c.mu.Unlock()
	return root
}
