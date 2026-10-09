package netupload

import "strings"

// ntToDOS переводит путь вида \Device\HarddiskVolume3\dir\a.pdf в C:\dir\a.pdf.
// devices — «устройство в нижнем регистре → буква диска». Совпадение только по
// границе компонента: том 3 не принимается за том 33.
func ntToDOS(path string, devices map[string]string) (string, bool) {
	lowered := strings.ToLower(path)
	for device, letter := range devices {
		if !strings.HasPrefix(lowered, device) {
			continue
		}
		rest := path[len(device):]
		if rest == "" {
			return letter + `\`, true
		}
		if rest[0] == '\\' {
			return letter + rest, true
		}
	}
	return "", false
}
