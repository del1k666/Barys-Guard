package netupload

import (
	"net/netip"
	"testing"
	"time"
)

func addr(text string) netip.Addr { return netip.MustParseAddr(text) }

func TestParseDNSResultsKeepsCNAMEsAndAddresses(t *testing.T) {
	names, addrs := ParseDNSResults("Content.Dropbox.com.", "type:  5 edge.dropbox.com;162.125.1.14;::ffff:162.125.1.15;")

	wantNames := []string{"content.dropbox.com", "edge.dropbox.com"}
	if len(names) != len(wantNames) || names[0] != wantNames[0] || names[1] != wantNames[1] {
		t.Fatalf("names = %v", names)
	}
	if len(addrs) != 2 || addrs[0] != addr("162.125.1.14") || addrs[1] != addr("162.125.1.15") {
		t.Fatalf("addrs = %v (IPv4-in-IPv6 приводится к IPv4)", addrs)
	}
}

func TestParseDNSResultsIgnoresGarbage(t *testing.T) {
	names, addrs := ParseDNSResults("", ";;мусор;type:;")
	if len(names) != 0 || len(addrs) != 0 {
		t.Fatalf("names=%v addrs=%v", names, addrs)
	}
}

func TestDNSCacheExpiresAndIsBounded(t *testing.T) {
	now := time.Unix(1_000, 0)
	cache := NewDNSCache(2)

	cache.Learn([]string{"a.example"}, []netip.Addr{addr("1.1.1.1")}, time.Minute, now)
	if got := cache.Names(addr("1.1.1.1"), now.Add(30*time.Second)); len(got) != 1 || got[0] != "a.example" {
		t.Fatalf("до истечения: %v", got)
	}
	if got := cache.Names(addr("1.1.1.1"), now.Add(2*time.Minute)); len(got) != 0 {
		t.Fatalf("после истечения: %v", got)
	}

	for i := 0; i < 10; i++ {
		cache.Learn([]string{"x"}, []netip.Addr{netip.AddrFrom4([4]byte{10, 0, 0, byte(i)})}, time.Hour, now)
	}
	if size := cache.Len(); size > 2 {
		t.Fatalf("кэш вырос до %d при пределе 2", size)
	}
}

func TestResolverPrefersDomainThenFallsBackToCIDR(t *testing.T) {
	res := &Resolver{Catalog: NewCatalog(DefaultServices()), DNS: NewDNSCache(100)}
	now := time.Unix(1_000, 0)
	res.DNS.Learn([]string{"www.dropbox.com"}, []netip.Addr{addr("162.125.1.14")}, time.Minute, now)

	svc, host, ok := res.Resolve(addr("162.125.1.14"), now)
	if !ok || svc.Key != "dropbox" || host != "www.dropbox.com" {
		t.Fatalf("по домену: %v %q %v", svc.Key, host, ok)
	}
	svc, host, ok = res.Resolve(addr("149.154.167.50"), now)
	if !ok || svc.Key != "telegram" || host != "" {
		t.Fatalf("по диапазону: %v %q %v", svc.Key, host, ok)
	}
	if _, _, ok := res.Resolve(addr("8.8.8.8"), now); ok {
		t.Fatal("неизвестный адрес не должен совпасть")
	}
}
