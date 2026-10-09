package netupload

import (
	"net/netip"
	"strings"
)

// DefaultServices — стартовый каталог. Он неполон по своей природе: сервисы
// меняют домены, поэтому каталог дополняется и заменяется конфигурацией с
// сервера (collectors.net_upload.services).
func DefaultServices() []Service {
	return []Service{
		{Key: "gdrive", Name: "Google Drive",
			Domains: []string{"drive.google.com", "docs.google.com", "drive.usercontent.google.com"}},
		{Key: "dropbox", Name: "Dropbox",
			Domains: []string{"dropbox.com", "dropboxapi.com", "dropboxusercontent.com"}},
		{Key: "onedrive", Name: "OneDrive / SharePoint",
			Domains: []string{"onedrive.live.com", "1drv.ms", "files.1drv.com", "sharepoint.com"}},
		{Key: "yadisk", Name: "Яндекс.Диск",
			Domains: []string{"disk.yandex.ru", "disk.yandex.com", "webdav.yandex.ru", "cloud-api.yandex.net", "yadi.sk"}},
		{Key: "mailru_cloud", Name: "Облако Mail.ru",
			Domains: []string{"cloud.mail.ru", "datacloudmail.ru"}},
		{Key: "telegram", Name: "Telegram",
			Domains: []string{"telegram.org", "t.me", "telegra.ph"},
			CIDRs: []string{
				"149.154.160.0/20", "91.108.4.0/22", "91.108.8.0/22", "91.108.12.0/22",
				"91.108.16.0/22", "91.108.20.0/22", "91.108.56.0/22",
				"2001:b28:f23d::/48", "2001:b28:f23f::/48", "2001:67c:4e8::/48",
			}},
		{Key: "whatsapp", Name: "WhatsApp",
			Domains: []string{"whatsapp.net", "whatsapp.com", "wa.me"}},
		{Key: "webmail", Name: "Веб-почта",
			Domains: []string{"mail.google.com", "outlook.office.com", "outlook.live.com", "mail.yandex.ru", "e.mail.ru"}},
	}
}

type netEntry struct {
	prefix  netip.Prefix
	service int
}

// Catalog находит сервис по домену или адресу.
type Catalog struct {
	services []Service
	nets     []netEntry
}

func NewCatalog(services []Service) *Catalog {
	c := &Catalog{services: services}
	for index, service := range services {
		for _, cidr := range service.CIDRs {
			prefix, err := netip.ParsePrefix(strings.TrimSpace(cidr))
			if err != nil {
				continue
			}
			c.nets = append(c.nets, netEntry{prefix: prefix.Masked(), service: index})
		}
	}
	return c
}

func normalizeHost(host string) string {
	return strings.TrimSuffix(strings.ToLower(strings.TrimSpace(host)), ".")
}

// MatchDomain сопоставляет по границе метки: dropbox.com и www.dropbox.com
// подходят, notdropbox.com и dropbox.com.evil.io — нет.
func (c *Catalog) MatchDomain(domain string) (Service, bool) {
	name := normalizeHost(domain)
	if name == "" {
		return Service{}, false
	}
	for _, service := range c.services {
		for _, root := range service.Domains {
			root = normalizeHost(root)
			if root != "" && (name == root || strings.HasSuffix(name, "."+root)) {
				return service, true
			}
		}
	}
	return Service{}, false
}

func (c *Catalog) MatchAddr(addr netip.Addr) (Service, bool) {
	addr = addr.Unmap()
	for _, entry := range c.nets {
		if entry.prefix.Contains(addr) {
			return c.services[entry.service], true
		}
	}
	return Service{}, false
}
