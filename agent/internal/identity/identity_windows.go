//go:build windows

package identity

import (
	"sync"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

const (
	wtsUserName   = 5
	wtsDomainName = 7
	noSession     = 0xFFFFFFFF
	// Пользователь консоли меняется редко, а опрашивается на каждое событие.
	consoleCacheTTL = 5 * time.Second
)

var (
	wtsapi              = windows.NewLazySystemDLL("wtsapi32.dll")
	procWTSQuerySesInfo = wtsapi.NewProc("WTSQuerySessionInformationW")
)

type resolver struct {
	mu        sync.Mutex
	cached    map[string]any
	cachedAt  time.Time
	hasCached bool
}

func New() Resolver { return &resolver{} }

func (*resolver) FileOwner(path string) map[string]any {
	descriptor, err := windows.GetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.OWNER_SECURITY_INFORMATION)
	if err != nil {
		return nil
	}
	owner, _, err := descriptor.Owner()
	if err != nil || owner == nil {
		return nil
	}
	name, domain, _, err := owner.LookupAccount("")
	if err != nil {
		// Учётная запись без имени (удалена): SID всё равно что-то значит.
		return map[string]any{"user_sid": owner.String(), "user_name": owner.String()}
	}
	return map[string]any{"user_sid": owner.String(), "user_name": domain + `\` + name}
}

func querySession(session, class uint32) string {
	var buffer *uint16
	var size uint32
	result, _, _ := procWTSQuerySesInfo.Call(0, uintptr(session), uintptr(class),
		uintptr(unsafe.Pointer(&buffer)), uintptr(unsafe.Pointer(&size)))
	if result == 0 || buffer == nil {
		return ""
	}
	defer windows.WTSFreeMemory(uintptr(unsafe.Pointer(buffer)))
	return windows.UTF16PtrToString(buffer)
}

func (r *resolver) ConsoleUser() map[string]any {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.hasCached && time.Since(r.cachedAt) < consoleCacheTTL {
		return r.cached
	}

	r.cached, r.cachedAt, r.hasCached = lookupConsoleUser(), time.Now(), true
	return r.cached
}

func lookupConsoleUser() map[string]any {
	session := windows.WTSGetActiveConsoleSessionId()
	if session == noSession {
		return nil
	}
	user := querySession(session, wtsUserName)
	if user == "" {
		return nil
	}
	domain := querySession(session, wtsDomainName)
	account := user
	if domain != "" {
		account = domain + `\` + user
	}

	actor := map[string]any{"user_name": account, "session_id": int(session)}
	if sid, _, _, err := windows.LookupSID("", account); err == nil {
		actor["user_sid"] = sid.String()
	} else {
		actor["user_sid"] = ""
	}
	return actor
}
