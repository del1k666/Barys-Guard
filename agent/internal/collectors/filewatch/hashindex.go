package filewatch

import (
	"container/list"
	"time"
)

type indexKey struct {
	sha  string
	size int64
}

type indexEntry struct {
	key  indexKey
	path string
	seen time.Time
}

// HashIndex помнит, где агент недавно видел файл с данным содержимым.
// Ограничен по числу записей и по сроку: он нужен только чтобы узнать
// источник копирования, а не как архив.
type HashIndex struct {
	max int
	ttl time.Duration
	ll  *list.List
	m   map[indexKey]*list.Element
}

func NewHashIndex(max int, ttl time.Duration) *HashIndex {
	return &HashIndex{max: max, ttl: ttl, ll: list.New(), m: map[indexKey]*list.Element{}}
}

func (h *HashIndex) Put(sha string, size int64, path string, now time.Time) {
	k := indexKey{sha, size}
	if element, ok := h.m[k]; ok {
		entry := element.Value.(*indexEntry)
		entry.path, entry.seen = path, now
		h.ll.MoveToFront(element)
		return
	}
	h.m[k] = h.ll.PushFront(&indexEntry{key: k, path: path, seen: now})
	for h.ll.Len() > h.max {
		oldest := h.ll.Back()
		delete(h.m, oldest.Value.(*indexEntry).key)
		h.ll.Remove(oldest)
	}
}

func (h *HashIndex) Lookup(sha string, size int64, now time.Time) (string, bool) {
	element, ok := h.m[indexKey{sha, size}]
	if !ok {
		return "", false
	}
	entry := element.Value.(*indexEntry)
	if now.Sub(entry.seen) > h.ttl {
		delete(h.m, entry.key)
		h.ll.Remove(element)
		return "", false
	}
	h.ll.MoveToFront(element)
	return entry.path, true
}
