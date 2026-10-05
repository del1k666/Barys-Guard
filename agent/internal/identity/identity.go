// Package identity определяет пользователя, от имени которого выполнено действие:
// владельца файла или пользователя активной консольной сессии.
package identity

import "strings"

// Resolver — то, что сборщики требуют от платформы.
type Resolver interface {
	// FileOwner — владелец файла или nil, если определить нельзя
	// (том без ACL, файл удалён, нет доступа).
	FileOwner(path string) map[string]any
	// ConsoleUser — пользователь активной консольной сессии или nil.
	ConsoleUser() map[string]any
}

// IsServiceSID отвечает, служебная ли это учётная запись: SYSTEM, LOCAL/NETWORK
// SERVICE, Administrators и TrustedInstaller. Такой владелец почти всегда значит
// «файл создан повышенным процессом», а не «его создал этот человек».
func IsServiceSID(sid string) bool {
	switch sid {
	case "S-1-5-18", "S-1-5-19", "S-1-5-20", "S-1-5-32-544":
		return true
	}
	return strings.HasPrefix(sid, "S-1-5-80-")
}

// Pick выбирает актёра события: настоящий владелец файла важнее пользователя
// консоли; для служебного или неизвестного владельца берётся пользователь консоли.
func Pick(owner, console map[string]any) map[string]any {
	if owner != nil {
		if sid, _ := owner["user_sid"].(string); !IsServiceSID(sid) {
			return owner
		}
	}
	if console != nil {
		return console
	}
	return owner
}
