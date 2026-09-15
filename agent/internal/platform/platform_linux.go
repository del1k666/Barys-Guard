//go:build linux

package platform

import (
	"fmt"
	"os"
	"strings"
)

type guard struct{}

func (guard) SecureDir(path string) error {
	if err := os.MkdirAll(path, 0o700); err != nil {
		return err
	}
	// MkdirAll не трогает права уже существующего каталога.
	return os.Chmod(path, 0o700)
}

func (guard) SecureFile(path string) error {
	return os.Chmod(path, 0o600)
}

func (guard) VerifySecure(path string) error {
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	if mode := info.Mode().Perm(); mode&0o077 != 0 {
		return fmt.Errorf("%w: %s имеет права %#o", ErrInsecurePermissions, path, mode)
	}
	return nil
}

func (guard) MachineID() (string, error) {
	// systemd пишет первый путь, dbus — второй. На системах без systemd
	// доступен только второй, и выдумывать значение вместо него нельзя:
	// сервер отсеивает дубли хостов именно по этому идентификатору.
	for _, path := range []string{"/etc/machine-id", "/var/lib/dbus/machine-id"} {
		raw, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		if id := strings.TrimSpace(string(raw)); id != "" {
			return id, nil
		}
	}
	return "", fmt.Errorf("machine-id не найден ни в /etc/machine-id, ни в /var/lib/dbus/machine-id")
}

func (guard) OSVersion() (string, error) {
	raw, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return "", err
	}
	for _, line := range strings.Split(string(raw), "\n") {
		value, found := strings.CutPrefix(strings.TrimSpace(line), "PRETTY_NAME=")
		if !found {
			continue
		}
		return strings.Trim(value, `"`), nil
	}
	return "linux", nil
}
