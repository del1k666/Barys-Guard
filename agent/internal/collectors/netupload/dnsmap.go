package netupload

import (
	"net/netip"
	"strings"
	"sync"
	"time"
)

// ParseDNSResults разбирает ответ DNS-клиента Windows. QueryResults — строка
// вида "type:  5 edge.example.com;1.2.3.4;::1;": CNAME с типом 5 и адреса через ";".
// Возвращает все имена цепочки (запрошенное и CNAME) и адреса (IPv4-in-IPv6
// приводится к IPv4).
func ParseDNSResults(queryName, results string) (names []string, addrs []netip.Addr) {
	if name := normalizeHost(queryName); name != "" {
		names = append(names, name)
	}
	for _, part := range strings.Split(results, ";") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if strings.HasPrefix(part, "type:") {
			fields := strings.Fields(part)
			if len(fields) >= 3 && fields[1] == "5" {
				names = append(names, normalizeHost(fields[2]))
			}
			continue
		}
		if parsed, err := netip.ParseAddr(part); err == nil {
			addrs = append(addrs, parsed.Unmap())
		}
	}
	return names, addrs
}

type dnsEntry struct {
	names   []string
	expires time.Time
}

// DNSCache помнит, какие имена вели на адрес. Размер ограничен: при переполнении
// сначала выбрасываются просроченные записи, затем любые.
type DNSCache struct {
	mu      sync.Mutex
	max     int
	entries map[netip.Addr]dnsEntry
}

func NewDNSCache(max int) *DNSCache {
	return &DNSCache{max: max, entries: map[netip.Addr]dnsEntry{}}
}

func (c *DNSCache) Learn(names []string, addrs []netip.Addr, ttl time.Duration, now time.Time) {
	if len(names) == 0 {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, addr := range addrs {
		addr = addr.Unmap()
		if _, known := c.entries[addr]; !known && len(c.entries) >= c.max {
			c.shrinkLocked(now)
		}
		c.entries[addr] = dnsEntry{names: names, expires: now.Add(ttl)}
	}
}

func (c *DNSCache) shrinkLocked(now time.Time) {
	for addr, entry := range c.entries {
		if !entry.expires.After(now) {
			delete(c.entries, addr)
		}
	}
	for addr := range c.entries {
		if len(c.entries) < c.max {
			return
		}
		delete(c.entries, addr)
	}
}

func (c *DNSCache) Names(addr netip.Addr, now time.Time) []string {
	c.mu.Lock()
	defer c.mu.Unlock()
	entry, ok := c.entries[addr.Unmap()]
	if !ok || !entry.expires.After(now) {
		return nil
	}
	return entry.names
}

func (c *DNSCache) Len() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.entries)
}

// Resolver определяет сервис адреса: сначала по именам из DNS, затем по
// диапазонам каталога (запасной путь, если DNS не виден — например, DoH).
type Resolver struct {
	Catalog *Catalog
	DNS     *DNSCache
}

func (r *Resolver) Resolve(addr netip.Addr, now time.Time) (Service, string, bool) {
	for _, name := range r.DNS.Names(addr, now) {
		if service, ok := r.Catalog.MatchDomain(name); ok {
			return service, name, true
		}
	}
	if service, ok := r.Catalog.MatchAddr(addr); ok {
		return service, "", true
	}
	return Service{}, "", false
}
