package netupload

import (
	"net/netip"
	"testing"
)

func TestMatchDomainOnLabelBoundary(t *testing.T) {
	cat := NewCatalog(DefaultServices())

	for _, domain := range []string{"dropbox.com", "www.dropbox.com", "WWW.Dropbox.COM.", "content.dropboxapi.com"} {
		svc, ok := cat.MatchDomain(domain)
		if !ok || svc.Key != "dropbox" {
			t.Errorf("%q -> %q, %v; ждали dropbox", domain, svc.Key, ok)
		}
	}
	for _, domain := range []string{"notdropbox.com", "dropbox.com.evil.io", "", "example.com"} {
		if svc, ok := cat.MatchDomain(domain); ok {
			t.Errorf("%q не должен совпасть, получили %q", domain, svc.Key)
		}
	}
}

func TestMatchAddrByCIDRAndMappedIPv6(t *testing.T) {
	cat := NewCatalog(DefaultServices())

	for _, text := range []string{"149.154.167.51", "::ffff:149.154.167.51", "91.108.56.10"} {
		svc, ok := cat.MatchAddr(netip.MustParseAddr(text))
		if !ok || svc.Key != "telegram" {
			t.Errorf("%s -> %q, %v; ждали telegram", text, svc.Key, ok)
		}
	}
	if _, ok := cat.MatchAddr(netip.MustParseAddr("8.8.8.8")); ok {
		t.Error("8.8.8.8 не относится ни к одному сервису")
	}
}

func TestBadCIDRIsSkipped(t *testing.T) {
	cat := NewCatalog([]Service{{Key: "x", Name: "x", CIDRs: []string{"мусор", "10.0.0.0/8"}}})

	if _, ok := cat.MatchAddr(netip.MustParseAddr("10.1.2.3")); !ok {
		t.Fatal("рабочий диапазон должен остаться")
	}
}

func TestEveryDefaultServiceIsUsable(t *testing.T) {
	seen := map[string]bool{}
	for _, svc := range DefaultServices() {
		if svc.Key == "" || svc.Name == "" || (len(svc.Domains) == 0 && len(svc.CIDRs) == 0) {
			t.Errorf("пустой сервис: %+v", svc)
		}
		if seen[svc.Key] {
			t.Errorf("повтор ключа %q", svc.Key)
		}
		seen[svc.Key] = true
	}
}
