package netupload

import (
	"net"
	"net/netip"
	"strconv"
	"strings"
	"sync"
)

// defaultMaxFileObjects — предел таблицы объектов файлов по умолчанию.
const defaultMaxFileObjects = 200_000

type fileEntry struct {
	path     string
	reported bool
}

// fileTable связывает объект файла из событий Kernel-File с путём (путь есть
// только в событии создания). Размер ограничен: при переполнении таблица
// сбрасывается, часть чтений будет пропущена, но память не растёт.
type fileTable struct {
	mu      sync.Mutex
	max     int
	entries map[uint64]*fileEntry
}

func newFileTable() *fileTable { return newFileTableLimit(defaultMaxFileObjects) }

// newFileTableLimit создаёт таблицу с заданным пределом (нужен тестам).
func newFileTableLimit(limit int) *fileTable {
	return &fileTable{max: limit, entries: map[uint64]*fileEntry{}}
}

func (t *fileTable) set(object uint64, path string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if _, exists := t.entries[object]; !exists && len(t.entries) >= t.max {
		t.entries = map[uint64]*fileEntry{}
	}
	t.entries[object] = &fileEntry{path: path}
}

// firstRead отдаёт путь только при первом чтении объекта: дальнейшие чтения того
// же файла процессом не нужны, а проверка размера (Stat) на каждом — дорога.
func (t *fileTable) firstRead(object uint64) (string, bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	entry := t.entries[object]
	if entry == nil || entry.reported {
		return "", false
	}
	entry.reported = true
	return entry.path, true
}

func (t *fileTable) drop(object uint64) {
	t.mu.Lock()
	delete(t.entries, object)
	t.mu.Unlock()
}

func (t *fileTable) size() int {
	t.mu.Lock()
	defer t.mu.Unlock()
	return len(t.entries)
}

// asUint приводит свойство события к uint64. Библиотека etw отдаёт все
// скалярные свойства строками (десятичными или вида "0x1f"); числовые типы
// принимаются для удобства тестов и на случай смены поведения библиотеки.
func asUint(value any) (uint64, bool) {
	switch v := value.(type) {
	case string:
		text := strings.TrimSpace(v)
		if text == "" {
			return 0, false
		}
		base := 10
		if strings.HasPrefix(text, "0x") || strings.HasPrefix(text, "0X") {
			base = 16
			text = text[2:]
		}
		parsed, err := strconv.ParseUint(text, base, 64)
		return parsed, err == nil
	case uint64:
		return v, true
	case uint32:
		return uint64(v), true
	case uint16:
		return uint64(v), true
	case uint8:
		return uint64(v), true
	case int:
		if v >= 0 {
			return uint64(v), true
		}
	case int64:
		if v >= 0 {
			return uint64(v), true
		}
	case int32:
		if v >= 0 {
			return uint64(v), true
		}
	}
	return 0, false
}

// asAddr приводит адрес назначения к netip.Addr: свойство приходит строкой
// (возможно, "ip:порт" или "[ipv6]:порт"), в тестах бывает net.IP или байтами.
func asAddr(value any) (netip.Addr, bool) {
	switch v := value.(type) {
	case net.IP:
		addr, ok := netip.AddrFromSlice(v)
		return addr.Unmap(), ok
	case []byte:
		addr, ok := netip.AddrFromSlice(v)
		return addr.Unmap(), ok
	case string:
		text := strings.TrimSpace(v)
		if addr, err := netip.ParseAddr(text); err == nil {
			return addr.Unmap(), true
		}
		if port, err := netip.ParseAddrPort(text); err == nil {
			return port.Addr().Unmap(), true
		}
	}
	return netip.Addr{}, false
}
