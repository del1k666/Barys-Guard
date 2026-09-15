# BarysGuard DLP — план 1B-agent: транспортное ядро Go-агента

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать конечному хосту работающего агента: он регистрируется по одноразовому токену, аутентифицируется клиентским сертификатом, шлёт heartbeat, забирает конфигурацию, исполняет команды оператора и продлевает сертификат.

**Architecture:** Отдельный Go-модуль, связанный с сервером только контрактом `api/gateway-v1.yaml`. Главная граница — `transport` знает про HTTP и ничего про расписание, `runner` знает про расписание и ничего про HTTP. Всё, что различается между Windows и Linux, живёт за интерфейсом `platform.Guard` и нигде больше.

**Tech Stack:** Go (директива `go 1.23`), стандартная библиотека, `golang.org/x/sys` (BSD-3), `gopkg.in/yaml.v3` только для тестов (MIT).

**Spec:** `docs/superpowers/specs/2026-09-15-agent-transport-design.md`

## Global Constraints

- Все команды выполняются из каталога `agent/`. Тулчейн — установленный `go`; директива модуля — `go 1.23`.
- Лицензии зависимостей — только пермиссивные. **GPL и AGPL запрещены.** Новых зависимостей сверх `golang.org/x/sys` и тестовой `gopkg.in/yaml.v3` этот план не вводит.
- Код и комментарии — на русском, как в серверных модулях. Комментарий объясняет **почему**, а не **что**.
- `go vet ./...` и `go test ./...` обязаны быть чистыми перед каждым коммитом.
- Сборка обязана проходить для обеих платформ: `GOOS=linux go build ./...` и `GOOS=windows go build ./...`.
- Личность агента определяется **серийным номером сертификата**. Агент никогда не сообщает свой `agent_id` в теле запроса как средство аутентификации.
- Приватный ключ генерируется на хосте и по сети не передаётся никогда. Субъект CSR сервером игнорируется.
- Файлы ключей пишутся **атомарно**: временный файл рядом, затем переименование поверх целевого.
- Каждое сообщение коммита завершается двумя строками:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01HyztBieur5cPEj5bnwEt9p
  ```
  Далее в плане они не повторяются в каждом блоке — добавлять их нужно всегда.
- Тесты, требующие Docker и живого сервера, помечаются build tag `e2e` и в обычный прогон не входят.

## Структура файлов

| Файл | Ответственность |
|---|---|
| `agent/go.mod` (создать) | Модуль `github.com/barysguard/agent` |
| `agent/internal/platform/platform.go` (создать) | Интерфейс `Guard` и общие ошибки |
| `agent/internal/platform/platform_linux.go` (создать) | Права `0600`/`0700`, `/etc/machine-id`, `/etc/os-release` |
| `agent/internal/platform/platform_windows.go` (создать) | DACL только для SYSTEM и Administrators, `MachineGuid`, `RtlGetVersion` |
| `agent/internal/hostfacts/hostfacts.go` (создать) | Сбор и усечение фактов хоста под лимиты контракта |
| `agent/internal/config/layout.go` (создать) | Раскладка каталогов, атомарная запись |
| `agent/internal/config/settings.go` (создать) | `agent.json` и `state.json` |
| `agent/internal/keystore/keystore.go` (создать) | Ключ, CSR, сертификат, срок продления |
| `agent/internal/transport/types.go` (создать) | Типы контракта |
| `agent/internal/transport/client.go` (создать) | TLS-клиент, ошибки статусов, доверие к CA |
| `agent/internal/transport/methods.go` (создать) | Семь методов протокола |
| `agent/internal/runner/backoff.go` (создать) | Откат с полным джиттером, джиттер интервала |
| `agent/internal/runner/commands.go` (создать) | Диспетчер команд и обработчики |
| `agent/internal/runner/agent.go` (создать) | Цикл heartbeat, продление, очередь результатов |
| `agent/cmd/barysguard-agent/main.go` (создать) | Подкоманды, сигналы, коды возврата |

Граф зависимостей ацикличен: `platform` ← `config` ← `keystore` ← `runner`, `transport` ← `runner`, `hostfacts` → `transport` и `platform`.

---

## Задача 1: Каркас модуля, платформенный слой и факты хоста

Всё, что различается между Windows и Linux, закрывается одним интерфейсом. Дальнейшие задачи про операционную систему не знают.

**Files:**
- Create: `agent/go.mod`
- Create: `agent/internal/platform/platform.go`
- Create: `agent/internal/platform/platform_linux.go`
- Create: `agent/internal/platform/platform_windows.go`
- Create: `agent/internal/hostfacts/hostfacts.go`
- Test: `agent/internal/platform/platform_test.go`
- Test: `agent/internal/hostfacts/hostfacts_test.go`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `platform.Guard` — интерфейс с методами `SecureDir(string) error`, `SecureFile(string) error`, `VerifySecure(string) error`, `MachineID() (string, error)`, `OSVersion() (string, error)`
  - `platform.New() Guard`
  - `platform.ErrInsecurePermissions` — часовой для проверки прав
  - `hostfacts.Facts` со всеми полями схемы `HostFacts` контракта
  - `hostfacts.Collect(g platform.Guard, agentVersion string) (Facts, error)`

- [x] **Шаг 1: Создать модуль**

```bash
mkdir -p agent/internal/platform agent/internal/hostfacts agent/cmd/barysguard-agent
cd agent
go mod init github.com/barysguard/agent
go get golang.org/x/sys@latest
```

Затем открыть `agent/go.mod` и убедиться, что директива версии — `go 1.23`. Если тулчейн записал более новую, исправить вручную: модуль обязан собираться средой сборки, а не только рабочей машиной.

- [x] **Шаг 2: Написать падающий тест**

Создать `agent/internal/platform/platform_test.go`:

```go
package platform_test

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/barysguard/agent/internal/platform"
)

func TestSecureDirCreatesDirectory(t *testing.T) {
	guard := platform.New()
	dir := filepath.Join(t.TempDir(), "pki")

	if err := guard.SecureDir(dir); err != nil {
		t.Fatalf("SecureDir: %v", err)
	}

	info, err := os.Stat(dir)
	if err != nil {
		t.Fatalf("Stat: %v", err)
	}
	if !info.IsDir() {
		t.Fatal("ожидался каталог")
	}
}

func TestSecureFileSurvivesVerification(t *testing.T) {
	guard := platform.New()
	dir := t.TempDir()
	path := filepath.Join(dir, "agent.key")
	if err := os.WriteFile(path, []byte("key"), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	if err := guard.SecureFile(path); err != nil {
		t.Fatalf("SecureFile: %v", err)
	}
	if err := guard.VerifySecure(path); err != nil {
		t.Fatalf("после SecureFile проверка обязана проходить: %v", err)
	}
}

func TestVerifySecureRejectsWorldReadableKey(t *testing.T) {
	// Ключ, который прочитал кто угодно, уже не доказывает личность.
	if runtime.GOOS == "windows" {
		t.Skip("режим доступа Unix неприменим к Windows")
	}
	guard := platform.New()
	path := filepath.Join(t.TempDir(), "agent.key")
	if err := os.WriteFile(path, []byte("key"), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	err := guard.VerifySecure(path)
	if err == nil {
		t.Fatal("ожидался отказ на права 0644")
	}
	if !strings.Contains(err.Error(), "0644") {
		t.Fatalf("ошибка обязана называть фактические права, получено: %v", err)
	}
}

func TestMachineIDIsStableAndNotEmpty(t *testing.T) {
	guard := platform.New()

	first, err := guard.MachineID()
	if err != nil {
		t.Fatalf("MachineID: %v", err)
	}
	if first == "" {
		t.Fatal("machine_id пуст")
	}

	second, _ := guard.MachineID()
	if first != second {
		// Меняющийся идентификатор превратил бы один хост в россыпь агентов.
		t.Fatalf("machine_id нестабилен: %q затем %q", first, second)
	}
}

func TestOSVersionIsNotEmpty(t *testing.T) {
	version, err := platform.New().OSVersion()
	if err != nil {
		t.Fatalf("OSVersion: %v", err)
	}
	if version == "" {
		t.Fatal("версия ОС пуста")
	}
}
```

- [x] **Шаг 3: Запустить тест и убедиться, что он падает**

```bash
go test ./internal/platform/ -v
```

Expected: FAIL — `no required module provides package .../internal/platform`.

- [x] **Шаг 4: Создать `agent/internal/platform/platform.go`**

```go
// Package platform закрывает всё, что различается между Windows и Linux.
// Остальные пакеты агента об операционной системе не знают.
package platform

import "errors"

// ErrInsecurePermissions возвращается, когда файл ключа доступен посторонним.
// Агент в этом случае отказывается стартовать — так же поступает ssh.
// Продолжать работу, делая вид, что скомпрометированный ключ доказывает
// личность, хуже, чем остановиться.
var ErrInsecurePermissions = errors.New("файл ключа доступен посторонним")

// Guard выставляет и подтверждает права на файлы ключей, а также добывает
// сведения о хосте, которые нельзя получить переносимым способом.
//
// VerifySecure существует отдельно от SecureFile намеренно: права,
// выставленные при записи, мог изменить кто угодно — ручное копирование
// каталога, восстановление из архива, неаккуратный установщик.
type Guard interface {
	SecureDir(path string) error
	SecureFile(path string) error
	VerifySecure(path string) error
	MachineID() (string, error)
	OSVersion() (string, error)
}

// New отдаёт реализацию для текущей операционной системы.
func New() Guard { return guard{} }
```

- [x] **Шаг 5: Создать `agent/internal/platform/platform_linux.go`**

```go
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
```

- [x] **Шаг 6: Создать `agent/internal/platform/platform_windows.go`**

```go
//go:build windows

package platform

import (
	"fmt"
	"os"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

// Полный доступ только SYSTEM (SY) и Administrators (BA). Буква P означает
// protected: наследование от родительского каталога отключено, иначе права,
// заданные на %ProgramData%, вернули бы доступ группе Users.
const restrictedSDDL = "D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"

type guard struct{}

func applyDACL(path string) error {
	descriptor, err := windows.SecurityDescriptorFromString(restrictedSDDL)
	if err != nil {
		return err
	}
	dacl, _, err := descriptor.DACL()
	if err != nil {
		return err
	}
	return windows.SetNamedSecurityInfo(
		path,
		windows.SE_FILE_OBJECT,
		windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION,
		nil, nil, dacl, nil,
	)
}

func (guard) SecureDir(path string) error {
	if err := os.MkdirAll(path, 0o700); err != nil {
		return err
	}
	return applyDACL(path)
}

func (guard) SecureFile(path string) error { return applyDACL(path) }

// Группы, присутствие которых в списке доступа означает, что ключ
// читает кто угодно из вошедших в систему.
func forbiddenSIDs() ([]*windows.SID, error) {
	var result []*windows.SID
	for _, known := range []windows.WELL_KNOWN_SID_TYPE{
		windows.WinWorldSid,
		windows.WinBuiltinUsersSid,
		windows.WinAuthenticatedUserSid,
	} {
		sid, err := windows.CreateWellKnownSid(known)
		if err != nil {
			return nil, err
		}
		result = append(result, sid)
	}
	return result, nil
}

func (guard) VerifySecure(path string) error {
	descriptor, err := windows.GetNamedSecurityInfo(
		path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION,
	)
	if err != nil {
		return err
	}
	dacl, _, err := descriptor.DACL()
	if err != nil {
		return err
	}
	forbidden, err := forbiddenSIDs()
	if err != nil {
		return err
	}

	for index := uint32(0); index < uint32(dacl.AceCount); index++ {
		var ace *windows.ACCESS_ALLOWED_ACE
		if err := windows.GetAce(dacl, index, &ace); err != nil {
			return err
		}
		sid := (*windows.SID)(unsafe.Pointer(&ace.SidStart))
		for _, bad := range forbidden {
			if sid.Equals(bad) {
				return fmt.Errorf("%w: %s доступен группе %s", ErrInsecurePermissions, path, bad)
			}
		}
	}
	return nil
}

func (guard) MachineID() (string, error) {
	// WOW64_64KEY обязателен: 32-битный процесс без него попадёт
	// в перенаправленный раздел реестра и прочтёт чужое значение.
	key, err := registry.OpenKey(
		registry.LOCAL_MACHINE,
		`SOFTWARE\Microsoft\Cryptography`,
		registry.QUERY_VALUE|registry.WOW64_64KEY,
	)
	if err != nil {
		return "", err
	}
	defer key.Close()

	guid, _, err := key.GetStringValue("MachineGuid")
	if err != nil {
		return "", err
	}
	if guid == "" {
		return "", fmt.Errorf("MachineGuid пуст")
	}
	return guid, nil
}

func (guard) OSVersion() (string, error) {
	// RtlGetVersion, а не GetVersionEx: последний с Windows 8.1 врёт
	// приложениям без манифеста совместимости.
	version := windows.RtlGetVersion()
	return fmt.Sprintf("%d.%d.%d", version.MajorVersion, version.MinorVersion, version.BuildNumber), nil
}
```

- [x] **Шаг 7: Запустить тесты платформы**

```bash
go test ./internal/platform/ -v
```

Expected: PASS, 5 тестов (один пропущен на Windows).

- [x] **Шаг 8: Написать падающий тест фактов хоста**

Создать `agent/internal/hostfacts/hostfacts_test.go`:

```go
package hostfacts_test

import (
	"errors"
	"runtime"
	"strings"
	"testing"

	"github.com/barysguard/agent/internal/hostfacts"
)

type stubGuard struct {
	machineID string
	osVersion string
	err       error
}

func (s stubGuard) SecureDir(string) error       { return nil }
func (s stubGuard) SecureFile(string) error      { return nil }
func (s stubGuard) VerifySecure(string) error    { return nil }
func (s stubGuard) MachineID() (string, error)   { return s.machineID, s.err }
func (s stubGuard) OSVersion() (string, error)   { return s.osVersion, nil }

func TestCollectFillsEveryContractField(t *testing.T) {
	facts, err := hostfacts.Collect(stubGuard{machineID: "abc", osVersion: "Ubuntu 24.04"}, "0.1.0")
	if err != nil {
		t.Fatalf("Collect: %v", err)
	}

	if facts.MachineID != "abc" {
		t.Fatalf("machine_id = %q", facts.MachineID)
	}
	if facts.OS != runtime.GOOS {
		t.Fatalf("os = %q, ожидалось %q", facts.OS, runtime.GOOS)
	}
	if facts.Arch != runtime.GOARCH {
		t.Fatalf("arch = %q", facts.Arch)
	}
	if facts.AgentVersion != "0.1.0" {
		t.Fatalf("agent_version = %q", facts.AgentVersion)
	}
	if facts.Hostname == "" {
		t.Fatal("hostname пуст")
	}
}

func TestCollectFailsWithoutMachineID(t *testing.T) {
	// Выдуманный идентификатор превратил бы один хост в россыпь
	// агентов-призраков: сервер отсеивает дубли именно по нему.
	_, err := hostfacts.Collect(stubGuard{err: errors.New("нет файла")}, "0.1.0")
	if err == nil {
		t.Fatal("ожидалась ошибка при недоступном machine_id")
	}
}

func TestLongOSVersionIsTruncatedToContractLimit(t *testing.T) {
	facts, err := hostfacts.Collect(
		stubGuard{machineID: "abc", osVersion: strings.Repeat("x", 500)}, "0.1.0",
	)
	if err != nil {
		t.Fatalf("Collect: %v", err)
	}
	// Контракт задаёт maxLength 128; сервер отверг бы запрос целиком.
	if len(facts.OSVersion) != 128 {
		t.Fatalf("длина os_version = %d, ожидалось 128", len(facts.OSVersion))
	}
}
```

- [x] **Шаг 9: Создать `agent/internal/hostfacts/hostfacts.go`**

```go
// Package hostfacts собирает сведения о хосте для регистрации агента.
package hostfacts

import (
	"fmt"
	"os"
	"runtime"

	"github.com/barysguard/agent/internal/platform"
)

// Пределы взяты из схемы HostFacts в api/gateway-v1.yaml. Превышение
// любого из них сервер отвергает целиком, поэтому усечение выполняется здесь.
const (
	maxMachineID    = 255
	maxHostname     = 255
	maxOS           = 32
	maxOSVersion    = 128
	maxArch         = 32
	maxAgentVersion = 32
)

// Facts соответствует схеме HostFacts контракта.
type Facts struct {
	MachineID    string `json:"machine_id"`
	Hostname     string `json:"hostname"`
	OS           string `json:"os"`
	OSVersion    string `json:"os_version"`
	Arch         string `json:"arch"`
	AgentVersion string `json:"agent_version"`
}

func truncate(value string, limit int) string {
	if len(value) <= limit {
		return value
	}
	return value[:limit]
}

// Collect собирает факты хоста. Отсутствие machine_id — ошибка, а не повод
// подставить случайное значение.
func Collect(guard platform.Guard, agentVersion string) (Facts, error) {
	machineID, err := guard.MachineID()
	if err != nil {
		return Facts{}, fmt.Errorf("machine_id недоступен: %w", err)
	}

	hostname, err := os.Hostname()
	if err != nil {
		return Facts{}, fmt.Errorf("hostname недоступен: %w", err)
	}

	// Версия ОС информативна, но не критична: без неё регистрация
	// всё равно должна состояться.
	osVersion, err := guard.OSVersion()
	if err != nil {
		osVersion = "unknown"
	}

	return Facts{
		MachineID:    truncate(machineID, maxMachineID),
		Hostname:     truncate(hostname, maxHostname),
		OS:           truncate(runtime.GOOS, maxOS),
		OSVersion:    truncate(osVersion, maxOSVersion),
		Arch:         truncate(runtime.GOARCH, maxArch),
		AgentVersion: truncate(agentVersion, maxAgentVersion),
	}, nil
}
```

- [x] **Шаг 10: Запустить тесты и проверить обе сборки**

```bash
go vet ./... && go test ./... -v
GOOS=linux   go build ./...
GOOS=windows go build ./...
```

Expected: PASS, 8 тестов; обе сборки без ошибок.

- [x] **Шаг 11: Зафиксировать**

```bash
cd ..
git add agent/go.mod agent/go.sum agent/internal/platform agent/internal/hostfacts
git commit -m "feat: agent platform layer and host facts"
```

---

## Задача 2: Раскладка каталогов, настройки и состояние

Настройки заполняет оператор, состояние пишет сам агент. Разделение файлов нужно, чтобы переустановка с готовым `agent.json` не затирала заработанную личность.

**Files:**
- Create: `agent/internal/config/layout.go`
- Create: `agent/internal/config/settings.go`
- Test: `agent/internal/config/config_test.go`

**Interfaces:**
- Consumes: `platform.Guard` из задачи 1.
- Produces:
  - `config.Layout` с методами `SettingsPath()`, `StatePath()`, `PKIDir()`, `KeyPath()`, `CertPath()`, `CAPath()` — все `string`
  - `config.NewLayout(dir string) Layout`, `config.DefaultDir() string`
  - `config.WriteAtomic(path string, data []byte, guard platform.Guard) error`
  - `config.Settings{ServerURL, LogLevel string}`, `LoadSettings(Layout) (Settings, error)`, `SaveSettings(Layout, Settings, platform.Guard) error`
  - `config.State{AgentID string; ConfigVersion int; Document map[string]any}`, `LoadState(Layout) (State, error)`, `SaveState(Layout, State, platform.Guard) error`

- [x] **Шаг 1: Написать падающий тест**

Создать `agent/internal/config/config_test.go`:

```go
package config_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/platform"
)

func TestLayoutPutsKeysUnderPKI(t *testing.T) {
	layout := config.NewLayout(filepath.Join("base"))

	if got, want := layout.PKIDir(), filepath.Join("base", "pki"); got != want {
		t.Fatalf("PKIDir = %q, ожидалось %q", got, want)
	}
	if got, want := layout.KeyPath(), filepath.Join("base", "pki", "agent.key"); got != want {
		t.Fatalf("KeyPath = %q, ожидалось %q", got, want)
	}
	if got, want := layout.SettingsPath(), filepath.Join("base", "agent.json"); got != want {
		t.Fatalf("SettingsPath = %q, ожидалось %q", got, want)
	}
}

func TestWriteAtomicLeavesNoTemporaryFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state.json")

	if err := config.WriteAtomic(path, []byte(`{"a":1}`), platform.New()); err != nil {
		t.Fatalf("WriteAtomic: %v", err)
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("ReadDir: %v", err)
	}
	if len(entries) != 1 {
		// Временный файл, оставшийся рядом, означает незавершённую запись.
		t.Fatalf("в каталоге %d записей, ожидалась одна", len(entries))
	}
}

func TestWriteAtomicReplacesExistingContent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	guard := platform.New()

	if err := config.WriteAtomic(path, []byte("старое"), guard); err != nil {
		t.Fatalf("WriteAtomic: %v", err)
	}
	if err := config.WriteAtomic(path, []byte("новое"), guard); err != nil {
		t.Fatalf("WriteAtomic повторно: %v", err)
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if string(raw) != "новое" {
		t.Fatalf("содержимое = %q", raw)
	}
}

func TestMissingStateIsEmptyNotError(t *testing.T) {
	// До регистрации файла состояния нет, и это нормальный ход событий.
	state, err := config.LoadState(config.NewLayout(t.TempDir()))
	if err != nil {
		t.Fatalf("LoadState: %v", err)
	}
	if state.AgentID != "" || state.ConfigVersion != 0 {
		t.Fatalf("ожидалось пустое состояние, получено %+v", state)
	}
}

func TestStateSurvivesRoundTrip(t *testing.T) {
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()
	want := config.State{
		AgentID:       "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f",
		ConfigVersion: 42,
		Document:      map[string]any{"logging": map[string]any{"level": "debug"}},
	}

	if err := config.SaveState(layout, want, guard); err != nil {
		t.Fatalf("SaveState: %v", err)
	}
	got, err := config.LoadState(layout)
	if err != nil {
		t.Fatalf("LoadState: %v", err)
	}

	if got.AgentID != want.AgentID || got.ConfigVersion != want.ConfigVersion {
		t.Fatalf("состояние не сохранилось: %+v", got)
	}
	// Документ нужен, чтобы If-None-Match имел смысл после перезапуска.
	logging, ok := got.Document["logging"].(map[string]any)
	if !ok || logging["level"] != "debug" {
		t.Fatalf("документ конфигурации потерян: %+v", got.Document)
	}
}

func TestSettingsSurviveRoundTrip(t *testing.T) {
	layout := config.NewLayout(t.TempDir())

	err := config.SaveSettings(layout, config.Settings{
		ServerURL: "https://dlp.example:8443",
		LogLevel:  "debug",
	}, platform.New())
	if err != nil {
		t.Fatalf("SaveSettings: %v", err)
	}

	got, err := config.LoadSettings(layout)
	if err != nil {
		t.Fatalf("LoadSettings: %v", err)
	}
	if got.ServerURL != "https://dlp.example:8443" || got.LogLevel != "debug" {
		t.Fatalf("настройки = %+v", got)
	}
}
```

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
go test ./internal/config/ -v
```

Expected: FAIL — пакет `internal/config` не существует.

- [x] **Шаг 3: Создать `agent/internal/config/layout.go`**

```go
// Package config задаёт раскладку файлов агента на диске и читает их.
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"

	"github.com/barysguard/agent/internal/platform"
)

// Layout — раскладка рабочего каталога агента.
type Layout struct{ Dir string }

func NewLayout(dir string) Layout { return Layout{Dir: dir} }

// DefaultDir отдаёт рабочий каталог по умолчанию для текущей платформы.
func DefaultDir() string {
	if runtime.GOOS == "windows" {
		if programData := os.Getenv("ProgramData"); programData != "" {
			return filepath.Join(programData, "BarysGuard")
		}
		return filepath.Join(`C:\ProgramData`, "BarysGuard")
	}
	return "/var/lib/barysguard"
}

// Настройки заполняет оператор, состояние пишет агент. Общий файл означал бы,
// что переустановка с готовыми настройками затирает личность агента.
func (l Layout) SettingsPath() string { return filepath.Join(l.Dir, "agent.json") }
func (l Layout) StatePath() string    { return filepath.Join(l.Dir, "state.json") }
func (l Layout) PKIDir() string       { return filepath.Join(l.Dir, "pki") }
func (l Layout) KeyPath() string      { return filepath.Join(l.PKIDir(), "agent.key") }
func (l Layout) CertPath() string     { return filepath.Join(l.PKIDir(), "agent.crt") }
func (l Layout) CAPath() string       { return filepath.Join(l.PKIDir(), "ca.crt") }

// WriteAtomic пишет во временный файл рядом и переименовывает поверх целевого.
//
// Прямая запись оставляет агента после обрыва питания с ключом от одного
// сертификата и телом другого — состоянием, из которого он не выйдет
// без переустановки.
func WriteAtomic(path string, data []byte, guard platform.Guard) error {
	if err := guard.SecureDir(filepath.Dir(path)); err != nil {
		return err
	}

	temporary, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".*")
	if err != nil {
		return err
	}
	name := temporary.Name()
	// Убирает временный файл на любом пути выхода, кроме успешного
	// переименования: после Rename файла с этим именем уже нет.
	defer os.Remove(name)

	if _, err := temporary.Write(data); err != nil {
		temporary.Close()
		return err
	}
	// Данные обязаны лечь на диск до переименования, иначе атомарность
	// имени не спасает от пустого файла после отключения питания.
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := guard.SecureFile(name); err != nil {
		return err
	}
	if err := os.Rename(name, path); err != nil {
		return fmt.Errorf("переименование %s в %s: %w", name, path, err)
	}
	return nil
}
```

- [x] **Шаг 4: Создать `agent/internal/config/settings.go`**

```go
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"

	"github.com/barysguard/agent/internal/platform"
)

// Settings — то, что задаёт оператор или установщик.
type Settings struct {
	ServerURL string `json:"server_url"`
	LogLevel  string `json:"log_level"`
}

// State — то, что агент заработал сам.
//
// Документ конфигурации хранится вместе с версией: без него заголовок
// If-None-Match бессмысленен, и после каждого перезапуска агент тянул бы
// полный документ заново.
type State struct {
	AgentID       string         `json:"agent_id"`
	ConfigVersion int            `json:"config_version"`
	Document      map[string]any `json:"document,omitempty"`
}

func readJSON(path string, target any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(raw, target); err != nil {
		return fmt.Errorf("разбор %s: %w", path, err)
	}
	return nil
}

func writeJSON(path string, value any, guard platform.Guard) error {
	raw, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return WriteAtomic(path, raw, guard)
}

func LoadSettings(layout Layout) (Settings, error) {
	var settings Settings
	if err := readJSON(layout.SettingsPath(), &settings); err != nil {
		return Settings{}, err
	}
	if settings.ServerURL == "" {
		return Settings{}, fmt.Errorf("%s не задаёт server_url", layout.SettingsPath())
	}
	return settings, nil
}

func SaveSettings(layout Layout, settings Settings, guard platform.Guard) error {
	return writeJSON(layout.SettingsPath(), settings, guard)
}

// LoadState не считает отсутствие файла ошибкой: до регистрации его нет.
func LoadState(layout Layout) (State, error) {
	var state State
	err := readJSON(layout.StatePath(), &state)
	if errors.Is(err, os.ErrNotExist) {
		return State{}, nil
	}
	if err != nil {
		return State{}, err
	}
	return state, nil
}

func SaveState(layout Layout, state State, guard platform.Guard) error {
	return writeJSON(layout.StatePath(), state, guard)
}
```

- [x] **Шаг 5: Запустить тесты**

```bash
go test ./internal/config/ -v
```

Expected: PASS, 6 тестов.

- [x] **Шаг 6: Зафиксировать**

```bash
cd .. && git add agent/internal/config && git commit -m "feat: agent on-disk layout with atomic writes"
```

---

## Задача 3: Keystore — ключ, CSR и сертификат

**Files:**
- Create: `agent/internal/keystore/keystore.go`
- Test: `agent/internal/keystore/keystore_test.go`

**Interfaces:**
- Consumes: `config.Layout`, `config.WriteAtomic`, `platform.Guard`.
- Produces:
  - `keystore.GenerateKey() (*ecdsa.PrivateKey, error)`
  - `keystore.EncodeKey(*ecdsa.PrivateKey) ([]byte, error)`
  - `keystore.CreateCSR(*ecdsa.PrivateKey) (string, error)` — PEM-строка
  - `keystore.Save(layout config.Layout, guard platform.Guard, keyPEM, certPEM []byte) error`
  - `keystore.SaveCA(layout config.Layout, guard platform.Guard, caPEM []byte) error`
  - `keystore.Load(layout config.Layout, guard platform.Guard) (tls.Certificate, *x509.Certificate, error)`
  - `keystore.LoadCAPool(layout config.Layout) (*x509.CertPool, error)`
  - `keystore.RenewalDue(cert *x509.Certificate, now time.Time) bool`

- [x] **Шаг 1: Написать падающий тест**

Создать `agent/internal/keystore/keystore_test.go`:

```go
package keystore_test

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/keystore"
	"github.com/barysguard/agent/internal/platform"
)

// issueCertificate выпускает самоподписанный сертификат с заданным сроком.
func issueCertificate(t *testing.T, key *ecdsa.PrivateKey, notBefore, notAfter time.Time) []byte {
	t.Helper()
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: "agent"},
		NotBefore:    notBefore,
		NotAfter:     notAfter,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatalf("CreateCertificate: %v", err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
}

func TestGeneratedKeyIsP256(t *testing.T) {
	key, err := keystore.GenerateKey()
	if err != nil {
		t.Fatalf("GenerateKey: %v", err)
	}
	if key.Curve != elliptic.P256() {
		t.Fatalf("кривая = %s, спека требует P-256", key.Curve.Params().Name)
	}
}

func TestCSRCarriesPublicKeyAndValidSignature(t *testing.T) {
	key, err := keystore.GenerateKey()
	if err != nil {
		t.Fatalf("GenerateKey: %v", err)
	}

	csrPEM, err := keystore.CreateCSR(key)
	if err != nil {
		t.Fatalf("CreateCSR: %v", err)
	}

	block, _ := pem.Decode([]byte(csrPEM))
	if block == nil {
		t.Fatal("CSR не является PEM")
	}
	csr, err := x509.ParseCertificateRequest(block.Bytes)
	if err != nil {
		t.Fatalf("ParseCertificateRequest: %v", err)
	}
	if err := csr.CheckSignature(); err != nil {
		t.Fatalf("подпись CSR недействительна: %v", err)
	}
	// Сервер подписывает именно открытый ключ из CSR, а субъект игнорирует.
	if !csr.PublicKey.(*ecdsa.PublicKey).Equal(&key.PublicKey) {
		t.Fatal("CSR несёт не тот открытый ключ")
	}
}

func TestSaveThenLoadReturnsUsablePair(t *testing.T) {
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()

	key, _ := keystore.GenerateKey()
	keyPEM, err := keystore.EncodeKey(key)
	if err != nil {
		t.Fatalf("EncodeKey: %v", err)
	}
	certPEM := issueCertificate(t, key, time.Now().Add(-time.Hour), time.Now().Add(24*time.Hour))

	if err := keystore.Save(layout, guard, keyPEM, certPEM); err != nil {
		t.Fatalf("Save: %v", err)
	}

	pair, leaf, err := keystore.Load(layout, guard)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(pair.Certificate) == 0 {
		t.Fatal("в паре нет сертификата")
	}
	if leaf.Subject.CommonName != "agent" {
		t.Fatalf("CN = %q", leaf.Subject.CommonName)
	}
}

func TestLoadRefusesWorldReadableKey(t *testing.T) {
	if os.Getenv("GOOS") == "windows" {
		t.Skip("режим доступа Unix неприменим к Windows")
	}
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()

	key, _ := keystore.GenerateKey()
	keyPEM, _ := keystore.EncodeKey(key)
	certPEM := issueCertificate(t, key, time.Now().Add(-time.Hour), time.Now().Add(24*time.Hour))
	if err := keystore.Save(layout, guard, keyPEM, certPEM); err != nil {
		t.Fatalf("Save: %v", err)
	}

	// Кто-то скопировал каталог и растерял права по дороге.
	if err := os.Chmod(layout.KeyPath(), 0o644); err != nil {
		t.Skipf("смена прав недоступна: %v", err)
	}

	if _, _, err := keystore.Load(layout, guard); err == nil {
		t.Fatal("загрузка обязана отказать при доступном посторонним ключе")
	}
}

func TestRenewalDueAtTwoThirdsOfLifetime(t *testing.T) {
	notBefore := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	notAfter := notBefore.Add(90 * 24 * time.Hour)
	cert := &x509.Certificate{NotBefore: notBefore, NotAfter: notAfter}

	cases := []struct {
		name string
		days int
		want bool
	}{
		{"свежий", 1, false},
		{"за день до порога", 59, false},
		{"на пороге 60 суток", 60, true},
		{"просрочен", 95, true},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			now := notBefore.Add(time.Duration(testCase.days) * 24 * time.Hour)
			if got := keystore.RenewalDue(cert, now); got != testCase.want {
				t.Fatalf("RenewalDue через %d суток = %v, ожидалось %v", testCase.days, got, testCase.want)
			}
		})
	}
}
```

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
go test ./internal/keystore/ -v
```

Expected: FAIL — пакет `internal/keystore` не существует.

- [x] **Шаг 3: Создать `agent/internal/keystore/keystore.go`**

```go
// Package keystore хранит ключ и сертификат агента на диске.
package keystore

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"os"
	"time"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/platform"
)

// Доля срока, после которой сертификат пора продлевать: 60 суток из 90.
const renewalNumerator, renewalDenominator = 2, 3

// GenerateKey создаёт ключ агента. Ключ генерируется на хосте и по сети
// не передаётся никогда — наружу уходит только CSR с открытой частью.
func GenerateKey() (*ecdsa.PrivateKey, error) {
	return ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
}

func EncodeKey(key *ecdsa.PrivateKey) ([]byte, error) {
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return nil, err
	}
	return pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}), nil
}

// CreateCSR формирует запрос на сертификат.
//
// Субъект произвольный: агент на этот момент ещё не знает своего agent_id,
// а сервер субъект запроса игнорирует и формирует собственный.
func CreateCSR(key *ecdsa.PrivateKey) (string, error) {
	template := &x509.CertificateRequest{
		Subject:            pkix.Name{CommonName: "barysguard-agent"},
		SignatureAlgorithm: x509.ECDSAWithSHA256,
	}
	der, err := x509.CreateCertificateRequest(rand.Reader, template, key)
	if err != nil {
		return "", err
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: der})), nil
}

// Save кладёт ключ и сертификат атомарно. Ключ пишется первым: пара,
// где сертификат новее ключа, нерабочая в любом случае, но обратный
// порядок оставил бы рабочим старый сертификат при сбое.
func Save(layout config.Layout, guard platform.Guard, keyPEM, certPEM []byte) error {
	if err := guard.SecureDir(layout.PKIDir()); err != nil {
		return err
	}
	if err := config.WriteAtomic(layout.KeyPath(), keyPEM, guard); err != nil {
		return fmt.Errorf("запись ключа: %w", err)
	}
	if err := config.WriteAtomic(layout.CertPath(), certPEM, guard); err != nil {
		return fmt.Errorf("запись сертификата: %w", err)
	}
	return nil
}

func SaveCA(layout config.Layout, guard platform.Guard, caPEM []byte) error {
	if err := guard.SecureDir(layout.PKIDir()); err != nil {
		return err
	}
	return config.WriteAtomic(layout.CAPath(), caPEM, guard)
}

// Load читает пару и разобранный сертификат.
//
// Проверка прав обязательна и выполняется до чтения: ключ, доступный
// посторонним, уже не доказывает личность агента.
func Load(layout config.Layout, guard platform.Guard) (tls.Certificate, *x509.Certificate, error) {
	if err := guard.VerifySecure(layout.KeyPath()); err != nil {
		return tls.Certificate{}, nil, err
	}

	pair, err := tls.LoadX509KeyPair(layout.CertPath(), layout.KeyPath())
	if err != nil {
		return tls.Certificate{}, nil, fmt.Errorf("загрузка пары ключ-сертификат: %w", err)
	}

	leaf, err := x509.ParseCertificate(pair.Certificate[0])
	if err != nil {
		return tls.Certificate{}, nil, fmt.Errorf("разбор сертификата: %w", err)
	}
	pair.Leaf = leaf
	return pair, leaf, nil
}

func LoadCAPool(layout config.Layout) (*x509.CertPool, error) {
	raw, err := os.ReadFile(layout.CAPath())
	if err != nil {
		return nil, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(raw) {
		return nil, fmt.Errorf("%s не содержит сертификатов PEM", layout.CAPath())
	}
	return pool, nil
}

// RenewalDue сообщает, истекло ли 2/3 срока сертификата.
//
// Отсчёт идёт по полям самого сертификата, а не по записанной дате выпуска:
// файл могли восстановить из резервной копии, и сохранённая отметка соврала бы.
func RenewalDue(cert *x509.Certificate, now time.Time) bool {
	lifetime := cert.NotAfter.Sub(cert.NotBefore)
	if lifetime <= 0 {
		return true
	}
	elapsed := now.Sub(cert.NotBefore)
	return elapsed*time.Duration(renewalDenominator) >= lifetime*time.Duration(renewalNumerator)
}
```

- [x] **Шаг 4: Запустить тесты**

```bash
go test ./internal/keystore/ -v
```

Expected: PASS, 5 тестов (один с подтестами).

- [x] **Шаг 5: Зафиксировать**

```bash
cd .. && git add agent/internal/keystore && git commit -m "feat: agent keystore with p256 keys and renewal threshold"
```

---

## Задача 4: Типы контракта и защита от расхождения

Структуры пишутся руками, но их соответствие `api/gateway-v1.yaml` проверяется тестом. Это ловит ровно ту ошибку, которую не находят на обычных тестах: поле переименовали на сервере, агент продолжает слать старое имя, сервер молча подставляет значение по умолчанию.

**Files:**
- Create: `agent/internal/transport/types.go`
- Test: `agent/internal/transport/contract_test.go`

**Interfaces:**
- Consumes: `hostfacts.Facts`.
- Produces: `transport.EnrollRequest`, `EnrollResponse`, `RenewRequest`, `RenewResponse`, `HeartbeatRequest`, `HeartbeatResponse`, `QueuedCommand`, `ConfigResponse`, `CommandResultRequest`; константы `StatusDone = "done"`, `StatusFailed = "failed"`.

- [x] **Шаг 1: Добавить тестовую зависимость**

```bash
cd agent && go get gopkg.in/yaml.v3@latest
```

- [x] **Шаг 2: Написать падающий тест**

Создать `agent/internal/transport/contract_test.go`:

```go
package transport_test

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/barysguard/agent/internal/transport"
)

type openAPI struct {
	Components struct {
		Schemas map[string]struct {
			Properties map[string]any `yaml:"properties"`
			Required   []string       `yaml:"required"`
		} `yaml:"schemas"`
	} `yaml:"components"`
}

func loadContract(t *testing.T) openAPI {
	t.Helper()
	// Контракт лежит вне модуля агента: он общий с сервером.
	path := filepath.Join("..", "..", "..", "api", "gateway-v1.yaml")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("чтение контракта %s: %v", path, err)
	}
	var document openAPI
	if err := yaml.Unmarshal(raw, &document); err != nil {
		t.Fatalf("разбор контракта: %v", err)
	}
	return document
}

// jsonFields собирает имена из тегов json структуры.
func jsonFields(value any) map[string]bool {
	fields := map[string]bool{}
	typ := reflect.TypeOf(value)
	for i := 0; i < typ.NumField(); i++ {
		tag := typ.Field(i).Tag.Get("json")
		if tag == "" || tag == "-" {
			continue
		}
		fields[strings.Split(tag, ",")[0]] = true
	}
	return fields
}

func TestStructsCoverEveryRequiredContractField(t *testing.T) {
	contract := loadContract(t)

	cases := []struct {
		schema string
		value  any
	}{
		{"EnrollRequest", transport.EnrollRequest{}},
		{"EnrollResponse", transport.EnrollResponse{}},
		{"RenewRequest", transport.RenewRequest{}},
		{"RenewResponse", transport.RenewResponse{}},
		{"HeartbeatRequest", transport.HeartbeatRequest{}},
		{"HeartbeatResponse", transport.HeartbeatResponse{}},
		{"QueuedCommand", transport.QueuedCommand{}},
		{"CommandResultRequest", transport.CommandResultRequest{}},
	}

	for _, testCase := range cases {
		t.Run(testCase.schema, func(t *testing.T) {
			schema, ok := contract.Components.Schemas[testCase.schema]
			if !ok {
				t.Fatalf("схемы %s нет в контракте", testCase.schema)
			}
			fields := jsonFields(testCase.value)
			for _, required := range schema.Required {
				if !fields[required] {
					t.Errorf("обязательное поле %q отсутствует в структуре Go", required)
				}
			}
		})
	}
}

func TestNoStructFieldIsAbsentFromContract(t *testing.T) {
	// Обратная проверка: поле, которого нет в контракте, сервер проигнорирует,
	// и агент будет считать, что сообщил то, чего не сообщал.
	contract := loadContract(t)

	cases := []struct {
		schema string
		value  any
	}{
		{"HeartbeatRequest", transport.HeartbeatRequest{}},
		{"EnrollRequest", transport.EnrollRequest{}},
		{"CommandResultRequest", transport.CommandResultRequest{}},
	}

	for _, testCase := range cases {
		t.Run(testCase.schema, func(t *testing.T) {
			schema := contract.Components.Schemas[testCase.schema]
			for field := range jsonFields(testCase.value) {
				if _, ok := schema.Properties[field]; !ok {
					t.Errorf("поле %q отсутствует в схеме %s контракта", field, testCase.schema)
				}
			}
		})
	}
}
```

- [x] **Шаг 3: Запустить тест и убедиться, что он падает**

```bash
go test ./internal/transport/ -v
```

Expected: FAIL — пакет `internal/transport` не существует.

- [x] **Шаг 4: Создать `agent/internal/transport/types.go`**

```go
// Package transport реализует протокол агента. Это единственный пакет,
// знающий про HTTP: расписание и повторы живут в runner.
package transport

import (
	"time"

	"github.com/barysguard/agent/internal/hostfacts"
)

// Значения поля status в CommandResultRequest. Контракт допускает только их.
const (
	StatusDone   = "done"
	StatusFailed = "failed"
)

type EnrollRequest struct {
	Token  string          `json:"token"`
	CSRPEM string          `json:"csr_pem"`
	Host   hostfacts.Facts `json:"host"`
}

type EnrollResponse struct {
	AgentID                  string `json:"agent_id"`
	CertificatePEM           string `json:"certificate_pem"`
	CAPEM                    string `json:"ca_pem"`
	ConfigVersion            int    `json:"config_version"`
	HeartbeatIntervalSeconds int    `json:"heartbeat_interval_seconds"`
}

type RenewRequest struct {
	CSRPEM string `json:"csr_pem"`
}

type RenewResponse struct {
	CertificatePEM string    `json:"certificate_pem"`
	CAPEM          string    `json:"ca_pem"`
	NotAfter       time.Time `json:"not_after"`
}

// HeartbeatRequest несёт поля буфера, которых у агента этого плана нет:
// контракт задаёт форму запроса, и соответствовать ей агент обязан
// независимо от собственной полноты. Отправляются нули.
type HeartbeatRequest struct {
	AgentVersion   string    `json:"agent_version"`
	ConfigVersion  int       `json:"config_version"`
	SentAt         time.Time `json:"sent_at"`
	BufferedEvents int       `json:"buffered_events"`
	BufferBytes    int       `json:"buffer_bytes"`
}

type QueuedCommand struct {
	ID        string         `json:"id"`
	Type      string         `json:"type"`
	Payload   map[string]any `json:"payload"`
	ExpiresAt time.Time      `json:"expires_at"`
}

type HeartbeatResponse struct {
	ServerTime               time.Time       `json:"server_time"`
	ConfigVersion            int             `json:"config_version"`
	HeartbeatIntervalSeconds int             `json:"heartbeat_interval_seconds"`
	Commands                 []QueuedCommand `json:"commands"`
}

type ConfigResponse struct {
	Version  int            `json:"version"`
	Document map[string]any `json:"document"`
}

type CommandResultRequest struct {
	Status string         `json:"status"`
	Result map[string]any `json:"result"`
}
```

- [x] **Шаг 5: Запустить тесты**

```bash
go test ./internal/transport/ -v
```

Expected: PASS, 2 теста с подтестами по каждой схеме.

- [x] **Шаг 6: Зафиксировать**

```bash
cd .. && git add agent/internal/transport agent/go.mod agent/go.sum
git commit -m "feat: agent contract types checked against the openapi document"
```

---

## Задача 5: TLS-клиент и доверие к CA

**Files:**
- Create: `agent/internal/transport/client.go`
- Test: `agent/internal/transport/client_test.go`
- Test: `agent/internal/transport/testca_test.go`

**Interfaces:**
- Consumes: типы задачи 4.
- Produces:
  - `transport.NewBootstrap(serverURL string, pool *x509.CertPool) (*Client, error)`
  - `transport.NewMutual(serverURL string, pool *x509.CertPool, pair tls.Certificate) (*Client, error)`
  - `transport.StatusError{Code int; RetryAfter time.Duration; Body string}` с методом `Error() string`
  - `transport.IsForbidden(err error) bool`
  - `transport.FetchCA(ctx context.Context, serverURL, pin string) ([]byte, error)`
  - `transport.PinOf(caPEM []byte) (string, error)`

- [x] **Шаг 1: Написать помощник выпуска сертификатов для тестов**

Создать `agent/internal/transport/testca_test.go`:

```go
package transport_test

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"testing"
	"time"
)

// testCA — настоящий удостоверяющий центр, живущий на время теста.
// Мок, принимающий любой запрос, подтвердил бы работоспособность
// неработающего агента, поэтому TLS здесь настоящий.
type testCA struct {
	cert *x509.Certificate
	key  *ecdsa.PrivateKey
	pem  []byte
}

func newTestCA(t *testing.T) *testCA {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("ключ CA: %v", err)
	}
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "BarysGuard Test CA"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
		BasicConstraintsValid: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatalf("сертификат CA: %v", err)
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatalf("разбор CA: %v", err)
	}
	return &testCA{
		cert: cert,
		key:  key,
		pem:  pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}),
	}
}

func (c *testCA) pool() *x509.CertPool {
	pool := x509.NewCertPool()
	pool.AddCert(c.cert)
	return pool
}

// issue выпускает конечный сертификат. serverName непустой делает его серверным.
func (c *testCA) issue(t *testing.T, commonName, serverName string) tls.Certificate {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("ключ: %v", err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(time.Now().UnixNano()),
		Subject:      pkix.Name{CommonName: commonName},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	if serverName != "" {
		template.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
		template.DNSNames = []string{serverName}
		template.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
	}

	der, err := x509.CreateCertificate(rand.Reader, template, c.cert, &key.PublicKey, c.key)
	if err != nil {
		t.Fatalf("выпуск сертификата: %v", err)
	}
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}
}
```

- [x] **Шаг 2: Написать падающий тест клиента**

Создать `agent/internal/transport/client_test.go`:

```go
package transport_test

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/transport"
)

// newTLSServer поднимает сервер, требующий клиентский сертификат.
func newTLSServer(t *testing.T, ca *testCA, handler http.Handler) *httptest.Server {
	t.Helper()
	server := httptest.NewUnstartedServer(handler)
	server.TLS = &tls.Config{
		Certificates: []tls.Certificate{ca.issue(t, "server", "localhost")},
		ClientCAs:    ca.pool(),
		ClientAuth:   tls.RequireAndVerifyClientCert,
		MinVersion:   tls.VersionTLS12,
	}
	server.StartTLS()
	t.Cleanup(server.Close)
	return server
}

func TestMutualClientPresentsItsCertificate(t *testing.T) {
	ca := newTestCA(t)
	var seenCN string
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Если бы клиент не предъявил сертификат, сюда бы не дошло.
		seenCN = r.TLS.PeerCertificates[0].Subject.CommonName
		w.Write([]byte(`{"status":"ok"}`))
	}))

	client, err := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent-42", ""))
	if err != nil {
		t.Fatalf("NewMutual: %v", err)
	}
	if _, err := client.Whoami(context.Background()); err != nil {
		t.Fatalf("Whoami: %v", err)
	}

	if seenCN != "agent-42" {
		t.Fatalf("сервер увидел CN %q, ожидался agent-42", seenCN)
	}
}

func TestBootstrapClientIsRejectedWhereCertificateIsRequired(t *testing.T) {
	ca := newTestCA(t)
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))

	client, err := transport.NewBootstrap(server.URL, ca.pool())
	if err != nil {
		t.Fatalf("NewBootstrap: %v", err)
	}
	// Бутстрапный клиент сертификата не имеет — рукопожатие обязано провалиться.
	if _, err := client.Whoami(context.Background()); err == nil {
		t.Fatal("ожидался отказ рукопожатия без клиентского сертификата")
	}
}

func TestUnknownCAIsRejected(t *testing.T) {
	ca := newTestCA(t)
	stranger := newTestCA(t)
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))

	client, err := transport.NewMutual(server.URL, stranger.pool(), ca.issue(t, "agent", ""))
	if err != nil {
		t.Fatalf("NewMutual: %v", err)
	}
	if _, err := client.Whoami(context.Background()); err == nil {
		t.Fatal("сертификат чужого CA обязан быть отвергнут")
	}
}

func TestStatusErrorCarriesCodeAndRetryAfter(t *testing.T) {
	ca := newTestCA(t)
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Retry-After", "17")
		w.WriteHeader(http.StatusTooManyRequests)
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	_, err := client.Whoami(context.Background())

	var statusErr *transport.StatusError
	if !errorsAs(err, &statusErr) {
		t.Fatalf("ожидался *StatusError, получено %T: %v", err, err)
	}
	if statusErr.Code != http.StatusTooManyRequests {
		t.Fatalf("код = %d", statusErr.Code)
	}
	// Своё представление о паузе агент обязан уступить серверному.
	if statusErr.RetryAfter != 17*time.Second {
		t.Fatalf("RetryAfter = %v, ожидалось 17s", statusErr.RetryAfter)
	}
}

func TestForbiddenIsRecognised(t *testing.T) {
	ca := newTestCA(t)
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	_, err := client.Whoami(context.Background())

	if !transport.IsForbidden(err) {
		t.Fatalf("403 обязан опознаваться: %v", err)
	}
}

func TestFetchCAAcceptsMatchingPin(t *testing.T) {
	ca := newTestCA(t)
	// GET /ca идёт без клиентского сертификата, поэтому сервер его не требует.
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(ca.pem)
	}))
	t.Cleanup(server.Close)

	block, _ := pem.Decode(ca.pem)
	sum := sha256.Sum256(block.Bytes)

	got, err := transport.FetchCA(context.Background(), server.URL, hex.EncodeToString(sum[:]))
	if err != nil {
		t.Fatalf("FetchCA: %v", err)
	}
	if string(got) != string(ca.pem) {
		t.Fatal("вернулся не тот CA")
	}
}

func TestFetchCARejectsWrongPin(t *testing.T) {
	// Смысл отпечатка в том, что подменённый CA не проходит.
	ca := newTestCA(t)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(ca.pem)
	}))
	t.Cleanup(server.Close)

	_, err := transport.FetchCA(context.Background(), server.URL, "00"+hex.EncodeToString(make([]byte, 31)))
	if err == nil {
		t.Fatal("несовпавший отпечаток обязан приводить к отказу")
	}
}

func TestFetchCADemandsAPin(t *testing.T) {
	// Скачать CA и тут же начать ему доверять — это тот самый перехват,
	// против которого вводится mTLS.
	if _, err := transport.FetchCA(context.Background(), "https://example.invalid", ""); err == nil {
		t.Fatal("без отпечатка загрузка CA обязана отказывать")
	}
}
```

Дописать в конец файла маленький помощник, чтобы не тянуть `errors` в каждый тест:

```go
func errorsAs(err error, target any) bool { return errors.As(err, target) }
```

и добавить `"errors"` в импорты файла.

- [x] **Шаг 3: Запустить тест и убедиться, что он падает**

```bash
go test ./internal/transport/ -run TestMutual -v
```

Expected: FAIL — `undefined: transport.NewMutual`.

- [x] **Шаг 4: Создать `agent/internal/transport/client.go`**

```go
package transport

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

const (
	requestTimeout = 30 * time.Second
	// Тело ошибки читается ограниченно: сервер за обратным прокси может
	// вернуть страницу в мегабайт, и складывать её в журнал незачем.
	maxErrorBody = 4 << 10
)

// StatusError — ответ сервера, отличный от успешного.
type StatusError struct {
	Code       int
	RetryAfter time.Duration
	Body       string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("сервер ответил %d: %s", e.Code, e.Body)
}

// IsForbidden отличает отзыв сертификата от прочих бед: повторять
// запрос в этом случае бессмысленно.
func IsForbidden(err error) bool {
	var statusErr *StatusError
	return errors.As(err, &statusErr) && statusErr.Code == http.StatusForbidden
}

// Client выполняет запросы к шлюзу. Создаётся в двух видах — бутстрапном
// и взаимном; см. NewBootstrap и NewMutual.
type Client struct {
	base *url.URL
	http *http.Client
}

func newClient(serverURL string, tlsConfig *tls.Config) (*Client, error) {
	base, err := url.Parse(serverURL)
	if err != nil {
		return nil, fmt.Errorf("разбор адреса сервера: %w", err)
	}
	transportConfig := http.DefaultTransport.(*http.Transport).Clone()
	transportConfig.TLSClientConfig = tlsConfig
	return &Client{
		base: base,
		http: &http.Client{Transport: transportConfig, Timeout: requestTimeout},
	}, nil
}

// NewBootstrap — клиент без собственного сертификата, для GET /ca и POST /enroll.
//
// Отдельный конструктор, а не необязательное поле: тип с опциональным
// сертификатом означал бы, что забытая настройка молча даёт анонимный
// запрос, а сервер отвечает 403 без объяснения причины.
func NewBootstrap(serverURL string, pool *x509.CertPool) (*Client, error) {
	return newClient(serverURL, &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12})
}

// NewMutual — клиент, предъявляющий сертификат агента.
func NewMutual(serverURL string, pool *x509.CertPool, pair tls.Certificate) (*Client, error) {
	return newClient(serverURL, &tls.Config{
		RootCAs:      pool,
		Certificates: []tls.Certificate{pair},
		MinVersion:   tls.VersionTLS12,
	})
}

func parseRetryAfter(value string) time.Duration {
	seconds, err := strconv.Atoi(value)
	if err != nil || seconds < 0 {
		return 0
	}
	return time.Duration(seconds) * time.Second
}

// do выполняет запрос и превращает неуспешный статус в *StatusError.
func (c *Client) do(ctx context.Context, req *http.Request) (*http.Response, error) {
	resp, err := c.http.Do(req.WithContext(ctx))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 200 && resp.StatusCode < 400 {
		return resp, nil
	}

	body, _ := io.ReadAll(io.LimitReader(resp.Body, maxErrorBody))
	resp.Body.Close()
	return nil, &StatusError{
		Code:       resp.StatusCode,
		RetryAfter: parseRetryAfter(resp.Header.Get("Retry-After")),
		Body:       string(body),
	}
}

// PinOf считает отпечаток SHA-256 от DER первого сертификата в PEM.
func PinOf(caPEM []byte) (string, error) {
	block, _ := pem.Decode(caPEM)
	if block == nil {
		return "", errors.New("ответ не является сертификатом PEM")
	}
	sum := sha256.Sum256(block.Bytes)
	return hex.EncodeToString(sum[:]), nil
}

// FetchCA забирает CA по GET /gateway/v1/ca и принимает его, только если
// отпечаток совпал с ожидаемым.
//
// Соединение здесь заведомо непроверяемо — доверять ещё нечему. Подлинность
// даёт не транспорт, а отпечаток: это ровно та же схема, по которой
// закрепляют ключ хоста в ssh. Пустой отпечаток поэтому недопустим.
func FetchCA(ctx context.Context, serverURL, pin string) ([]byte, error) {
	if pin == "" {
		return nil, errors.New("загрузка CA по сети требует отпечатка --ca-pin")
	}

	base, err := url.Parse(serverURL)
	if err != nil {
		return nil, err
	}
	client := &http.Client{
		Timeout: requestTimeout,
		Transport: &http.Transport{
			// Проверка цепочки невозможна: проверяем отпечаток ниже.
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, //nolint:gosec
		},
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, base.JoinPath("gateway", "v1", "ca").String(), nil)
	if err != nil {
		return nil, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, &StatusError{Code: resp.StatusCode}
	}
	caPEM, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, err
	}

	actual, err := PinOf(caPEM)
	if err != nil {
		return nil, err
	}
	if actual != pin {
		return nil, fmt.Errorf("отпечаток CA %s не совпал с ожидаемым %s", actual, pin)
	}
	return caPEM, nil
}
```

- [x] **Шаг 5: Запустить тесты**

Тесты клиента обращаются к `Whoami`, который появится в задаче 6. Сейчас прогон обязан падать именно на этом:

```bash
go test ./internal/transport/ -v
```

Expected: FAIL — `client.Whoami undefined`. Это ожидаемо и закрывается следующей задачей.

- [x] **Шаг 6: Зафиксировать**

```bash
cd .. && git add agent/internal/transport
git commit -m "feat: agent tls client with pinned ca bootstrap"
```

---

## Задача 6: Методы протокола

**Files:**
- Create: `agent/internal/transport/methods.go`
- Test: `agent/internal/transport/methods_test.go`

**Interfaces:**
- Consumes: `Client`, типы задачи 4.
- Produces (методы `*Client`):
  - `Whoami(ctx) (map[string]any, error)`
  - `Enroll(ctx, EnrollRequest) (EnrollResponse, error)`
  - `Renew(ctx, csrPEM string) (RenewResponse, error)`
  - `Heartbeat(ctx, HeartbeatRequest) (HeartbeatResponse, error)`
  - `Config(ctx, etag string) (ConfigResponse, bool, error)` — второе значение `true`, если сервер ответил `304`
  - `CommandResult(ctx, commandID string, req CommandResultRequest) error`

- [x] **Шаг 1: Написать падающий тест**

Создать `agent/internal/transport/methods_test.go`:

```go
package transport_test

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/hostfacts"
	"github.com/barysguard/agent/internal/transport"
)

func TestEnrollSendsTokenAndCSR(t *testing.T) {
	ca := newTestCA(t)
	var received transport.EnrollRequest
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&received)
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(transport.EnrollResponse{
			AgentID:                  "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f",
			CertificatePEM:           "cert",
			CAPEM:                    "ca",
			ConfigVersion:            77,
			HeartbeatIntervalSeconds: 30,
		})
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	response, err := client.Enroll(context.Background(), transport.EnrollRequest{
		Token:  "BG-ENROLL-AAAA",
		CSRPEM: "-----BEGIN CERTIFICATE REQUEST-----",
		Host:   hostfacts.Facts{MachineID: "m-1", Hostname: "ws-1"},
	})
	if err != nil {
		t.Fatalf("Enroll: %v", err)
	}

	if received.Token != "BG-ENROLL-AAAA" {
		t.Fatalf("сервер получил токен %q", received.Token)
	}
	if received.Host.MachineID != "m-1" {
		t.Fatalf("machine_id не доехал: %+v", received.Host)
	}
	if response.ConfigVersion != 77 {
		t.Fatalf("config_version = %d", response.ConfigVersion)
	}
}

func TestHeartbeatRoundTripsCommands(t *testing.T) {
	ca := newTestCA(t)
	var received transport.HeartbeatRequest
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&received)
		json.NewEncoder(w).Encode(transport.HeartbeatResponse{
			ServerTime:               time.Now().UTC(),
			ConfigVersion:            5,
			HeartbeatIntervalSeconds: 45,
			Commands: []transport.QueuedCommand{
				{ID: "c-1", Type: "ping", Payload: map[string]any{}, ExpiresAt: time.Now().Add(time.Hour)},
			},
		})
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	response, err := client.Heartbeat(context.Background(), transport.HeartbeatRequest{
		AgentVersion:  "0.1.0",
		ConfigVersion: 4,
		SentAt:        time.Now().UTC(),
	})
	if err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}

	if received.AgentVersion != "0.1.0" {
		t.Fatalf("agent_version = %q", received.AgentVersion)
	}
	if response.HeartbeatIntervalSeconds != 45 {
		t.Fatalf("интервал = %d", response.HeartbeatIntervalSeconds)
	}
	if len(response.Commands) != 1 || response.Commands[0].Type != "ping" {
		t.Fatalf("команды = %+v", response.Commands)
	}
}

func TestConfigSendsIfNoneMatchAndReportsNotModified(t *testing.T) {
	ca := newTestCA(t)
	var seenETag string
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seenETag = r.Header.Get("If-None-Match")
		w.WriteHeader(http.StatusNotModified)
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	_, notModified, err := client.Config(context.Background(), `"42"`)
	if err != nil {
		// 304 — это не ошибка, и превращаться в неё по дороге не должно.
		t.Fatalf("Config: %v", err)
	}

	if seenETag != `"42"` {
		t.Fatalf("If-None-Match = %q", seenETag)
	}
	if !notModified {
		t.Fatal("ожидался признак «не изменилось»")
	}
}

func TestConfigReturnsDocument(t *testing.T) {
	ca := newTestCA(t)
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("ETag", `"9"`)
		json.NewEncoder(w).Encode(transport.ConfigResponse{
			Version:  9,
			Document: map[string]any{"logging": map[string]any{"level": "debug"}},
		})
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	response, notModified, err := client.Config(context.Background(), "")
	if err != nil {
		t.Fatalf("Config: %v", err)
	}
	if notModified {
		t.Fatal("документ пришёл, признак «не изменилось» неуместен")
	}
	if response.Version != 9 {
		t.Fatalf("версия = %d", response.Version)
	}
}

func TestCommandResultTargetsItsCommand(t *testing.T) {
	ca := newTestCA(t)
	var path string
	var received transport.CommandResultRequest
	server := newTLSServer(t, ca, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		json.NewDecoder(r.Body).Decode(&received)
		w.WriteHeader(http.StatusAccepted)
	}))

	client, _ := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	err := client.CommandResult(context.Background(), "c-9", transport.CommandResultRequest{
		Status: transport.StatusDone,
		Result: map[string]any{"pong": true},
	})
	if err != nil {
		t.Fatalf("CommandResult: %v", err)
	}

	if path != "/gateway/v1/commands/c-9/result" {
		t.Fatalf("путь = %q", path)
	}
	if received.Status != "done" {
		t.Fatalf("status = %q", received.Status)
	}
}
```

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
go test ./internal/transport/ -v
```

Expected: FAIL — `client.Enroll undefined`.

- [x] **Шаг 3: Создать `agent/internal/transport/methods.go`**

```go
package transport

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

// request выполняет запрос с телом JSON и разбирает ответ в out.
// Если out равен nil, тело ответа отбрасывается.
func (c *Client) request(ctx context.Context, method string, segments []string, body, out any) error {
	var reader *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(raw)
	} else {
		reader = bytes.NewReader(nil)
	}

	req, err := http.NewRequest(method, c.base.JoinPath(segments...).String(), reader)
	if err != nil {
		return err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.do(ctx, req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if out == nil {
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("разбор ответа %s: %w", req.URL.Path, err)
	}
	return nil
}

// Whoami подтверждает, что сервер узнаёт агента по его сертификату.
func (c *Client) Whoami(ctx context.Context) (map[string]any, error) {
	var out map[string]any
	err := c.request(ctx, http.MethodGet, []string{"gateway", "v1", "whoami"}, nil, &out)
	return out, err
}

func (c *Client) Enroll(ctx context.Context, in EnrollRequest) (EnrollResponse, error) {
	var out EnrollResponse
	err := c.request(ctx, http.MethodPost, []string{"gateway", "v1", "enroll"}, in, &out)
	return out, err
}

func (c *Client) Renew(ctx context.Context, csrPEM string) (RenewResponse, error) {
	var out RenewResponse
	err := c.request(ctx, http.MethodPost, []string{"gateway", "v1", "renew"}, RenewRequest{CSRPEM: csrPEM}, &out)
	return out, err
}

func (c *Client) Heartbeat(ctx context.Context, in HeartbeatRequest) (HeartbeatResponse, error) {
	var out HeartbeatResponse
	err := c.request(ctx, http.MethodPost, []string{"gateway", "v1", "heartbeat"}, in, &out)
	return out, err
}

// Config забирает документ конфигурации. Второе значение равно true,
// если сервер ответил 304 и документ у агента уже актуален.
//
// Реализовано отдельно от request: 304 не несёт тела, и общий разбор
// JSON на нём споткнулся бы.
func (c *Client) Config(ctx context.Context, etag string) (ConfigResponse, bool, error) {
	req, err := http.NewRequest(http.MethodGet, c.base.JoinPath("gateway", "v1", "config").String(), nil)
	if err != nil {
		return ConfigResponse{}, false, err
	}
	if etag != "" {
		req.Header.Set("If-None-Match", etag)
	}

	resp, err := c.do(ctx, req)
	if err != nil {
		return ConfigResponse{}, false, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotModified {
		return ConfigResponse{}, true, nil
	}

	var out ConfigResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return ConfigResponse{}, false, fmt.Errorf("разбор конфигурации: %w", err)
	}
	return out, false, nil
}

func (c *Client) CommandResult(ctx context.Context, commandID string, in CommandResultRequest) error {
	return c.request(
		ctx, http.MethodPost,
		[]string{"gateway", "v1", "commands", commandID, "result"},
		in, nil,
	)
}
```

- [x] **Шаг 4: Запустить весь пакет транспорта**

```bash
go test ./internal/transport/ -v
```

Expected: PASS — тесты задач 4, 5 и 6 целиком.

- [x] **Шаг 5: Зафиксировать**

```bash
cd .. && git add agent/internal/transport
git commit -m "feat: agent protocol methods with etag revalidation"
```

---

## Задача 7: Откат с полным джиттером

**Files:**
- Create: `agent/internal/runner/backoff.go`
- Test: `agent/internal/runner/backoff_test.go`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `runner.Backoff` с полями `Base`, `Max time.Duration`
  - `runner.NewBackoff(base, max time.Duration, source rand.Source) *Backoff`
  - `(*Backoff).Next() time.Duration`, `(*Backoff).Reset()`
  - `runner.JitterInterval(d time.Duration, random *rand.Rand) time.Duration`
  - Константы `DefaultBackoffBase = time.Second`, `DefaultBackoffMax = 300 * time.Second`

- [x] **Шаг 1: Написать падающий тест**

Создать `agent/internal/runner/backoff_test.go`:

```go
package runner_test

import (
	"math/rand"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/runner"
)

func TestBackoffNeverExceedsGrowingCeiling(t *testing.T) {
	backoff := runner.NewBackoff(time.Second, 300*time.Second, rand.NewSource(1))

	// Полный джиттер: значение лежит в [0, min(max, base*2^n)].
	ceilings := []time.Duration{1, 2, 4, 8, 16, 32}
	for attempt, ceiling := range ceilings {
		got := backoff.Next()
		if got < 0 || got > ceiling*time.Second {
			t.Fatalf("попытка %d: пауза %v вне [0, %v]", attempt, got, ceiling*time.Second)
		}
	}
}

func TestBackoffRespectsCeiling(t *testing.T) {
	backoff := runner.NewBackoff(time.Second, 10*time.Second, rand.NewSource(2))

	for i := 0; i < 50; i++ {
		if got := backoff.Next(); got > 10*time.Second {
			t.Fatalf("пауза %v превысила потолок", got)
		}
	}
}

func TestBackoffIsRandomAcrossSources(t *testing.T) {
	// Без джиттера пять тысяч агентов после перезапуска сервера
	// сохраняют форму волны и укладывают его повторно.
	first := runner.NewBackoff(time.Second, 300*time.Second, rand.NewSource(1))
	second := runner.NewBackoff(time.Second, 300*time.Second, rand.NewSource(2))

	same := 0
	for i := 0; i < 8; i++ {
		if first.Next() == second.Next() {
			same++
		}
	}
	if same == 8 {
		t.Fatal("две независимые последовательности совпали целиком")
	}
}

func TestResetReturnsToTheFirstStep(t *testing.T) {
	backoff := runner.NewBackoff(time.Second, 300*time.Second, rand.NewSource(3))
	for i := 0; i < 6; i++ {
		backoff.Next()
	}

	backoff.Reset()
	if got := backoff.Next(); got > time.Second {
		t.Fatalf("после сброса пауза %v, ожидалось не больше базовой", got)
	}
}

func TestJitterIntervalStaysWithinTenPercent(t *testing.T) {
	random := rand.New(rand.NewSource(4))
	base := 30 * time.Second

	for i := 0; i < 200; i++ {
		got := runner.JitterInterval(base, random)
		if got < 27*time.Second || got > 33*time.Second {
			t.Fatalf("интервал %v вне ±10%% от %v", got, base)
		}
	}
}

func TestJitterIntervalActuallyVaries(t *testing.T) {
	// Флот, развёрнутый одной волной, обязан разойтись во времени.
	random := rand.New(rand.NewSource(5))
	seen := map[time.Duration]bool{}
	for i := 0; i < 50; i++ {
		seen[runner.JitterInterval(30*time.Second, random)] = true
	}
	if len(seen) < 5 {
		t.Fatalf("получено лишь %d различных интервалов", len(seen))
	}
}
```

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
go test ./internal/runner/ -v
```

Expected: FAIL — пакет `internal/runner` не существует.

- [x] **Шаг 3: Создать `agent/internal/runner/backoff.go`**

```go
// Package runner отвечает за расписание работы агента: цикл heartbeat,
// паузы при сбоях и исполнение команд. Про HTTP он не знает ничего.
package runner

import (
	"math/rand"
	"time"
)

const (
	DefaultBackoffBase = time.Second
	DefaultBackoffMax  = 300 * time.Second

	// Доля разброса установившегося интервала опроса.
	intervalJitterPercent = 10
)

// Backoff выдаёт паузы по схеме полного джиттера: random(0, min(max, base·2^n)).
//
// Полного, а не половинного: при неполном джиттере переподключающийся флот
// сохраняет форму волны, и сервер, только что поднявшийся, ложится снова.
type Backoff struct {
	Base    time.Duration
	Max     time.Duration
	random  *rand.Rand
	attempt int
}

// NewBackoff принимает источник случайности параметром: иначе джиттер
// непроверяем — тест либо принимает любой результат, либо становится хлопающим.
func NewBackoff(base, max time.Duration, source rand.Source) *Backoff {
	return &Backoff{Base: base, Max: max, random: rand.New(source)}
}

func (b *Backoff) Next() time.Duration {
	ceiling := b.Base << b.attempt
	// Сдвиг переполняется быстрее, чем достигается разумный потолок.
	if ceiling <= 0 || ceiling > b.Max {
		ceiling = b.Max
	} else {
		b.attempt++
	}
	return time.Duration(b.random.Int63n(int64(ceiling) + 1))
}

func (b *Backoff) Reset() { b.attempt = 0 }

// JitterInterval разбрасывает установившийся интервал опроса на ±10%.
//
// Откат при сбоях эту задачу не решает: он включается только при ошибках,
// а синхронная волна возникает при совершенно штатной работе флота,
// развёрнутого за одну минуту.
func JitterInterval(interval time.Duration, random *rand.Rand) time.Duration {
	spread := int64(interval) * intervalJitterPercent / 100
	if spread <= 0 {
		return interval
	}
	return interval + time.Duration(random.Int63n(2*spread+1)-spread)
}
```

- [x] **Шаг 4: Запустить тесты**

```bash
go test ./internal/runner/ -v
```

Expected: PASS, 6 тестов.

- [x] **Шаг 5: Зафиксировать**

```bash
cd .. && git add agent/internal/runner
git commit -m "feat: agent backoff with full jitter"
```

---

## Задача 8: Диспетчер команд

**Files:**
- Create: `agent/internal/runner/commands.go`
- Test: `agent/internal/runner/commands_test.go`

**Interfaces:**
- Consumes: `transport.QueuedCommand`, `transport.CommandResultRequest`, `transport.StatusDone`, `transport.StatusFailed`.
- Produces:
  - `runner.Handler func(ctx context.Context, payload map[string]any) (map[string]any, error)`
  - `runner.Dispatcher map[string]Handler` с методом `Execute(ctx, transport.QueuedCommand) transport.CommandResultRequest`
  - `runner.MaxResultBytes = 64 * 1024`
  - `runner.Diagnostics` — структура с полями `AgentVersion string`, `StartedAt time.Time`, `CertNotAfter time.Time`, `LastHeartbeatAt time.Time`, `ConfigVersion int`
  - `runner.NewDispatcher(refresh func(context.Context) (int, error), diagnostics func() Diagnostics) Dispatcher`

- [x] **Шаг 1: Написать падающий тест**

Создать `agent/internal/runner/commands_test.go`:

```go
package runner_test

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/runner"
	"github.com/barysguard/agent/internal/transport"
)

func testDispatcher() runner.Dispatcher {
	return runner.NewDispatcher(
		func(context.Context) (int, error) { return 21, nil },
		func() runner.Diagnostics {
			return runner.Diagnostics{
				AgentVersion:    "0.1.0",
				StartedAt:       time.Now().Add(-90 * time.Second),
				CertNotAfter:    time.Now().Add(80 * 24 * time.Hour),
				LastHeartbeatAt: time.Now(),
				ConfigVersion:   21,
			}
		},
	)
}

func TestPingAnswersPong(t *testing.T) {
	result := testDispatcher().Execute(context.Background(), transport.QueuedCommand{
		ID: "c-1", Type: "ping", Payload: map[string]any{},
	})

	if result.Status != transport.StatusDone {
		t.Fatalf("status = %q", result.Status)
	}
	if result.Result["pong"] != true {
		t.Fatalf("результат = %+v", result.Result)
	}
}

func TestRefreshConfigReportsVersion(t *testing.T) {
	result := testDispatcher().Execute(context.Background(), transport.QueuedCommand{
		ID: "c-2", Type: "refresh_config", Payload: map[string]any{},
	})

	if result.Status != transport.StatusDone {
		t.Fatalf("status = %q, тело %+v", result.Status, result.Result)
	}
	if result.Result["config_version"] != 21 {
		t.Fatalf("config_version = %v", result.Result["config_version"])
	}
}

func TestCollectDiagnosticsReportsUptimeAndCertificate(t *testing.T) {
	result := testDispatcher().Execute(context.Background(), transport.QueuedCommand{
		ID: "c-3", Type: "collect_diagnostics", Payload: map[string]any{},
	})

	if result.Status != transport.StatusDone {
		t.Fatalf("status = %q", result.Status)
	}
	for _, key := range []string{"agent_version", "os", "arch", "uptime_seconds", "cert_not_after", "config_version"} {
		if _, ok := result.Result[key]; !ok {
			t.Errorf("в диагностике нет поля %q", key)
		}
	}
}

func TestUnknownCommandFailsWithoutPanicking(t *testing.T) {
	// Сервер новее агента вправе прислать команду, о которой агент не знает.
	result := testDispatcher().Execute(context.Background(), transport.QueuedCommand{
		ID: "c-4", Type: "self_destruct", Payload: map[string]any{},
	})

	if result.Status != transport.StatusFailed {
		t.Fatalf("status = %q, ожидался failed", result.Status)
	}
	if !strings.Contains(result.Result["error"].(string), "self_destruct") {
		t.Fatalf("ошибка обязана называть тип команды: %+v", result.Result)
	}
}

func TestFailingHandlerBecomesFailedResult(t *testing.T) {
	dispatcher := runner.Dispatcher{
		"boom": func(context.Context, map[string]any) (map[string]any, error) {
			return nil, errors.New("диск недоступен")
		},
	}

	result := dispatcher.Execute(context.Background(), transport.QueuedCommand{ID: "c-5", Type: "boom"})

	if result.Status != transport.StatusFailed {
		t.Fatalf("status = %q", result.Status)
	}
	// Молчание оставило бы команду висеть на сервере до истечения TTL.
	if result.Result["error"] != "диск недоступен" {
		t.Fatalf("текст ошибки потерян: %+v", result.Result)
	}
}

func TestOversizedResultIsTruncated(t *testing.T) {
	dispatcher := runner.Dispatcher{
		"flood": func(context.Context, map[string]any) (map[string]any, error) {
			return map[string]any{"blob": strings.Repeat("x", runner.MaxResultBytes*2)}, nil
		},
	}

	result := dispatcher.Execute(context.Background(), transport.QueuedCommand{ID: "c-6", Type: "flood"})

	// Сервер отвергает результат крупнее MAX_RESULT_BYTES целиком;
	// диагностика не является каналом передачи артефактов.
	if result.Result["truncated"] != true {
		t.Fatalf("крупный результат обязан быть усечён: %+v", result.Result)
	}
	if _, ok := result.Result["blob"]; ok {
		t.Fatal("после усечения исходное поле остаться не должно")
	}
}
```

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
go test ./internal/runner/ -run TestPing -v
```

Expected: FAIL — `undefined: runner.NewDispatcher`.

- [x] **Шаг 3: Создать `agent/internal/runner/commands.go`**

```go
package runner

import (
	"context"
	"encoding/json"
	"fmt"
	"runtime"
	"time"

	"github.com/barysguard/agent/internal/transport"
)

// Предел совпадает с MAX_RESULT_BYTES сервера. Более крупный результат
// сервер отвергает целиком, поэтому усечение выполняется здесь.
const MaxResultBytes = 64 * 1024

// Handler исполняет одну команду.
type Handler func(ctx context.Context, payload map[string]any) (map[string]any, error)

// Dispatcher отделён от цикла, чтобы боевые команды подпроекта 2
// добавлялись без правки расписания.
type Dispatcher map[string]Handler

// Diagnostics — снимок состояния агента для команды collect_diagnostics.
type Diagnostics struct {
	AgentVersion    string
	StartedAt       time.Time
	CertNotAfter    time.Time
	LastHeartbeatAt time.Time
	ConfigVersion   int
}

// NewDispatcher собирает набор команд этого плана.
//
// refresh принудительно забирает конфигурацию и отдаёт её версию;
// diagnostics отдаёт снимок состояния. Оба переданы функциями, чтобы
// диспетчер не зависел ни от транспорта, ни от цикла.
func NewDispatcher(
	refresh func(context.Context) (int, error),
	diagnostics func() Diagnostics,
) Dispatcher {
	return Dispatcher{
		"ping": func(context.Context, map[string]any) (map[string]any, error) {
			return map[string]any{
				"pong":       true,
				"agent_time": time.Now().UTC().Format(time.RFC3339),
			}, nil
		},
		"refresh_config": func(ctx context.Context, _ map[string]any) (map[string]any, error) {
			version, err := refresh(ctx)
			if err != nil {
				return nil, err
			}
			return map[string]any{"config_version": version}, nil
		},
		"collect_diagnostics": func(context.Context, map[string]any) (map[string]any, error) {
			snapshot := diagnostics()
			return map[string]any{
				"agent_version":     snapshot.AgentVersion,
				"os":                runtime.GOOS,
				"arch":              runtime.GOARCH,
				"uptime_seconds":    int(time.Since(snapshot.StartedAt).Seconds()),
				"cert_not_after":    snapshot.CertNotAfter.UTC().Format(time.RFC3339),
				"last_heartbeat_at": snapshot.LastHeartbeatAt.UTC().Format(time.RFC3339),
				"config_version":    snapshot.ConfigVersion,
			}, nil
		},
	}
}

// fits сообщает, помещается ли результат в предел сервера.
func fits(result map[string]any) bool {
	raw, err := json.Marshal(result)
	return err == nil && len(raw) <= MaxResultBytes
}

// Execute исполняет команду и всегда возвращает результат, пригодный к отправке.
//
// Неизвестный тип — это failed с внятным текстом, а не падение цикла:
// правило совместимости допускает сервер новее агента.
func (d Dispatcher) Execute(ctx context.Context, command transport.QueuedCommand) transport.CommandResultRequest {
	handler, known := d[command.Type]
	if !known {
		return transport.CommandResultRequest{
			Status: transport.StatusFailed,
			Result: map[string]any{"error": fmt.Sprintf("команда %q агенту неизвестна", command.Type)},
		}
	}

	result, err := handler(ctx, command.Payload)
	if err != nil {
		return transport.CommandResultRequest{
			Status: transport.StatusFailed,
			Result: map[string]any{"error": err.Error()},
		}
	}
	if result == nil {
		result = map[string]any{}
	}
	if !fits(result) {
		result = map[string]any{
			"truncated": true,
			"note":      "результат превысил предел сервера и опущен",
		}
	}
	return transport.CommandResultRequest{Status: transport.StatusDone, Result: result}
}
```

- [x] **Шаг 4: Запустить тесты**

```bash
go test ./internal/runner/ -v
```

Expected: PASS, 12 тестов (задачи 7 и 8).

- [x] **Шаг 5: Зафиксировать**

```bash
cd .. && git add agent/internal/runner
git commit -m "feat: agent command dispatcher with result size guard"
```

---

## Задача 9: Цикл heartbeat, продление и очередь результатов

Сердце агента. Здесь сходятся транспорт, состояние и расписание.

**Files:**
- Create: `agent/internal/runner/agent.go`
- Test: `agent/internal/runner/agent_test.go`

**Interfaces:**
- Consumes: всё предыдущее.
- Produces:
  - `runner.Options{ServerURL string; AgentVersion string; Layout config.Layout; Guard platform.Guard; Client *transport.Client; Random rand.Source; Now func() time.Time}`
  - `runner.New(opts Options) (*Agent, error)`
  - `(*Agent).RunOnce(ctx context.Context) (time.Duration, error)` — один проход, возвращает паузу до следующего
  - `(*Agent).Run(ctx context.Context) error`
  - `(*Agent).MaybeRenew(ctx context.Context, leaf *x509.Certificate) error`
  - `runner.ErrRevoked` — часовой для `403`
  - `runner.MaxPendingResults = 100`

- [x] **Шаг 1: Написать падающий тест**

Создать `agent/internal/runner/agent_test.go`:

```go
package runner_test

import (
	"context"
	"encoding/json"
	"math/rand"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/runner"
	"github.com/barysguard/agent/internal/transport"
)

// newAgent собирает агента поверх заданного обработчика HTTP.
func newAgent(t *testing.T, handler http.Handler, state config.State) (*runner.Agent, config.Layout) {
	t.Helper()
	ca := newRunnerCA(t)
	server := newRunnerTLSServer(t, ca, handler)

	layout := config.NewLayout(t.TempDir())
	guard := platform.New()
	if err := config.SaveState(layout, state, guard); err != nil {
		t.Fatalf("SaveState: %v", err)
	}

	client, err := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	if err != nil {
		t.Fatalf("NewMutual: %v", err)
	}

	agent, err := runner.New(runner.Options{
		ServerURL:    server.URL,
		AgentVersion: "0.1.0",
		Layout:       layout,
		Guard:        guard,
		Client:       client,
		Random:       rand.NewSource(1),
	})
	if err != nil {
		t.Fatalf("runner.New: %v", err)
	}
	return agent, layout
}

func TestRunOnceAppliesServerInterval(t *testing.T) {
	agent, _ := newAgent(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(transport.HeartbeatResponse{
			ServerTime:               time.Now().UTC(),
			ConfigVersion:            0,
			HeartbeatIntervalSeconds: 60,
			Commands:                 []transport.QueuedCommand{},
		})
	}), config.State{ConfigVersion: 0})

	pause, err := agent.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}

	// Интервал сервера плюс джиттер до ±10%.
	if pause < 54*time.Second || pause > 66*time.Second {
		t.Fatalf("пауза %v вне окрестности 60s", pause)
	}
}

func TestConfigIsFetchedWhenVersionDiffers(t *testing.T) {
	var configRequests atomic.Int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/gateway/v1/heartbeat":
			json.NewEncoder(w).Encode(transport.HeartbeatResponse{
				ServerTime:               time.Now().UTC(),
				ConfigVersion:            99,
				HeartbeatIntervalSeconds: 30,
				Commands:                 []transport.QueuedCommand{},
			})
		case "/gateway/v1/config":
			configRequests.Add(1)
			w.Header().Set("ETag", `"99"`)
			json.NewEncoder(w).Encode(transport.ConfigResponse{
				Version:  99,
				Document: map[string]any{"logging": map[string]any{"level": "debug"}},
			})
		}
	})

	agent, layout := newAgent(t, handler, config.State{ConfigVersion: 1})

	if _, err := agent.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if configRequests.Load() != 1 {
		t.Fatalf("запросов конфигурации: %d, ожидался один", configRequests.Load())
	}

	// Версия сохранена — второй проход за документом не пойдёт.
	if _, err := agent.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce повторно: %v", err)
	}
	if configRequests.Load() != 1 {
		t.Fatalf("документ запрошен повторно при совпавшей версии: %d", configRequests.Load())
	}

	state, err := config.LoadState(layout)
	if err != nil {
		t.Fatalf("LoadState: %v", err)
	}
	if state.ConfigVersion != 99 {
		t.Fatalf("версия в состоянии = %d", state.ConfigVersion)
	}
}

func TestCommandIsExecutedAndItsResultSent(t *testing.T) {
	var resultBody atomic.Value
	delivered := false
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/gateway/v1/heartbeat":
			commands := []transport.QueuedCommand{}
			if !delivered {
				delivered = true
				commands = append(commands, transport.QueuedCommand{
					ID: "c-1", Type: "ping", Payload: map[string]any{},
					ExpiresAt: time.Now().Add(time.Hour),
				})
			}
			json.NewEncoder(w).Encode(transport.HeartbeatResponse{
				ServerTime:               time.Now().UTC(),
				HeartbeatIntervalSeconds: 30,
				Commands:                 commands,
			})
		case r.URL.Path == "/gateway/v1/commands/c-1/result":
			var body transport.CommandResultRequest
			json.NewDecoder(r.Body).Decode(&body)
			resultBody.Store(body)
			w.WriteHeader(http.StatusAccepted)
		}
	})

	agent, _ := newAgent(t, handler, config.State{})
	if _, err := agent.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}

	stored, ok := resultBody.Load().(transport.CommandResultRequest)
	if !ok {
		t.Fatal("результат команды не отправлен")
	}
	if stored.Status != transport.StatusDone || stored.Result["pong"] != true {
		t.Fatalf("результат = %+v", stored)
	}
}

func TestUndeliveredResultIsRetriedOnTheNextPass(t *testing.T) {
	var attempts atomic.Int32
	delivered := false
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/gateway/v1/heartbeat":
			commands := []transport.QueuedCommand{}
			if !delivered {
				delivered = true
				commands = append(commands, transport.QueuedCommand{
					ID: "c-2", Type: "ping", Payload: map[string]any{},
					ExpiresAt: time.Now().Add(time.Hour),
				})
			}
			json.NewEncoder(w).Encode(transport.HeartbeatResponse{
				ServerTime:               time.Now().UTC(),
				HeartbeatIntervalSeconds: 30,
				Commands:                 commands,
			})
		case r.URL.Path == "/gateway/v1/commands/c-2/result":
			// Первая попытка обрывается, вторая обязана состояться.
			if attempts.Add(1) == 1 {
				w.WriteHeader(http.StatusBadGateway)
				return
			}
			w.WriteHeader(http.StatusAccepted)
		}
	})

	agent, _ := newAgent(t, handler, config.State{})
	agent.RunOnce(context.Background())
	agent.RunOnce(context.Background())

	if attempts.Load() < 2 {
		t.Fatalf("повтор не состоялся, попыток: %d", attempts.Load())
	}
}

func TestForbiddenStopsTheAgent(t *testing.T) {
	// Отзыв сертификата обязан прекращать работу, а не порождать вечные повторы.
	agent, _ := newAgent(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}), config.State{})

	_, err := agent.RunOnce(context.Background())
	if err == nil {
		t.Fatal("ожидалась ошибка на 403")
	}
	if !errorsIs(err, runner.ErrRevoked) {
		t.Fatalf("403 обязан опознаваться как отзыв: %v", err)
	}
}

func TestServerErrorProducesBackoffNotFailure(t *testing.T) {
	agent, _ := newAgent(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}), config.State{})

	pause, err := agent.RunOnce(context.Background())
	if err == nil {
		t.Fatal("ожидалась ошибка на 500")
	}
	if errorsIs(err, runner.ErrRevoked) {
		t.Fatal("500 — это не отзыв сертификата")
	}
	if pause <= 0 || pause > runner.DefaultBackoffMax {
		t.Fatalf("пауза отката %v вне разумных пределов", pause)
	}
}

func TestRetryAfterOverridesBackoff(t *testing.T) {
	agent, _ := newAgent(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Retry-After", "42")
		w.WriteHeader(http.StatusTooManyRequests)
	}), config.State{})

	pause, _ := agent.RunOnce(context.Background())
	if pause != 42*time.Second {
		t.Fatalf("пауза %v, сервер просил 42s", pause)
	}
}
```

Дописать в конец файла помощники и импорты, общие с тестами транспорта:

```go
func errorsIs(err, target error) bool { return errors.Is(err, target) }
```

плюс `"errors"` в импорты. Помощники `newRunnerCA`, `newRunnerTLSServer` — копии `newTestCA` и `newTLSServer` из задачи 5, перенесённые в `agent/internal/runner/testca_test.go`: пакеты разные, и экспортировать тестовый CA ради этого не стоит.

- [x] **Шаг 2: Скопировать помощник тестового CA**

Создать `agent/internal/runner/testca_test.go` — тот же код, что в `agent/internal/transport/testca_test.go` из задачи 5, с двумя переименованиями: `newTestCA` → `newRunnerCA`, `newTLSServer` → `newRunnerTLSServer`, и `package runner_test` в первой строке.

- [x] **Шаг 3: Запустить тест и убедиться, что он падает**

```bash
go test ./internal/runner/ -run TestRunOnce -v
```

Expected: FAIL — `undefined: runner.New`.

- [x] **Шаг 4: Создать `agent/internal/runner/agent.go`**

```go
package runner

import (
	"context"
	"crypto/x509"
	"errors"
	"fmt"
	"log/slog"
	"math/rand"
	"time"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/keystore"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/transport"
)

// ErrRevoked означает, что сервер отказал в обслуживании: сертификат отозван
// либо не признан. Повторять запрос бессмысленно.
var ErrRevoked = errors.New("сервер отказал в обслуживании")

// Дальше сотни неотданных результатов копить незачем: команда, чей результат
// не удалось отдать за столько циклов, на сервере уже истекла.
const MaxPendingResults = 100

type pendingResult struct {
	commandID string
	body      transport.CommandResultRequest
}

type Options struct {
	ServerURL    string
	AgentVersion string
	Layout       config.Layout
	Guard        platform.Guard
	Client       *transport.Client
	Random       rand.Source
	// Now подменяется в тестах продления; при nil берётся time.Now.
	Now func() time.Time
}

type Agent struct {
	options    Options
	state      config.State
	backoff    *Backoff
	random     *rand.Rand
	dispatcher Dispatcher
	pending    []pendingResult

	startedAt       time.Time
	lastHeartbeatAt time.Time
	certNotAfter    time.Time
}

func New(options Options) (*Agent, error) {
	if options.Client == nil {
		return nil, errors.New("клиент транспорта не задан")
	}
	if options.Random == nil {
		options.Random = rand.NewSource(time.Now().UnixNano())
	}
	if options.Now == nil {
		options.Now = time.Now
	}

	state, err := config.LoadState(options.Layout)
	if err != nil {
		return nil, fmt.Errorf("чтение состояния: %w", err)
	}

	agent := &Agent{
		options:   options,
		state:     state,
		backoff:   NewBackoff(DefaultBackoffBase, DefaultBackoffMax, options.Random),
		random:    rand.New(options.Random),
		startedAt: options.Now(),
	}
	// Диспетчер замыкается на агента: обе функции обращаются к его состоянию.
	agent.dispatcher = NewDispatcher(agent.refreshConfig, agent.diagnostics)
	return agent, nil
}

func (a *Agent) diagnostics() Diagnostics {
	return Diagnostics{
		AgentVersion:    a.options.AgentVersion,
		StartedAt:       a.startedAt,
		CertNotAfter:    a.certNotAfter,
		LastHeartbeatAt: a.lastHeartbeatAt,
		ConfigVersion:   a.state.ConfigVersion,
	}
}

// refreshConfig забирает документ, игнорируя сохранённую версию.
func (a *Agent) refreshConfig(ctx context.Context) (int, error) {
	response, _, err := a.options.Client.Config(ctx, "")
	if err != nil {
		return 0, err
	}
	a.state.ConfigVersion = response.Version
	a.state.Document = response.Document
	if err := config.SaveState(a.options.Layout, a.state, a.options.Guard); err != nil {
		return 0, err
	}
	return response.Version, nil
}

// syncConfig забирает документ, только если версия разошлась.
func (a *Agent) syncConfig(ctx context.Context, serverVersion int) error {
	if serverVersion == a.state.ConfigVersion && a.state.Document != nil {
		return nil
	}

	etag := fmt.Sprintf("%q", a.state.ConfigVersion)
	response, notModified, err := a.options.Client.Config(ctx, etag)
	if err != nil {
		return err
	}
	if notModified {
		return nil
	}

	a.state.ConfigVersion = response.Version
	a.state.Document = response.Document
	return config.SaveState(a.options.Layout, a.state, a.options.Guard)
}

// flushPending досылает результаты, не ушедшие в прошлые проходы.
func (a *Agent) flushPending(ctx context.Context) {
	remaining := a.pending[:0]
	for _, item := range a.pending {
		if err := a.options.Client.CommandResult(ctx, item.commandID, item.body); err != nil {
			slog.Warn("результат команды не отправлен", "command_id", item.commandID, "error", err)
			remaining = append(remaining, item)
		}
	}
	a.pending = remaining
}

func (a *Agent) enqueue(item pendingResult) {
	if len(a.pending) >= MaxPendingResults {
		// Самый старый результат к этому моменту уже не нужен никому.
		a.pending = a.pending[1:]
	}
	a.pending = append(a.pending, item)
}

// pauseFor вычисляет паузу после неудачи.
func (a *Agent) pauseFor(err error) time.Duration {
	var statusErr *transport.StatusError
	if errors.As(err, &statusErr) && statusErr.RetryAfter > 0 {
		// Своё представление о паузе агент уступает серверному.
		return statusErr.RetryAfter
	}
	return a.backoff.Next()
}

// RunOnce выполняет один проход цикла и возвращает паузу до следующего.
func (a *Agent) RunOnce(ctx context.Context) (time.Duration, error) {
	response, err := a.options.Client.Heartbeat(ctx, transport.HeartbeatRequest{
		AgentVersion:  a.options.AgentVersion,
		ConfigVersion: a.state.ConfigVersion,
		SentAt:        a.options.Now().UTC(),
		// Буфера в этом плане нет, но контракт задаёт форму запроса.
		BufferedEvents: 0,
		BufferBytes:    0,
	})
	if err != nil {
		if transport.IsForbidden(err) {
			return 0, fmt.Errorf("%w: %v", ErrRevoked, err)
		}
		return a.pauseFor(err), err
	}

	a.backoff.Reset()
	a.lastHeartbeatAt = a.options.Now()

	if err := a.syncConfig(ctx, response.ConfigVersion); err != nil {
		// Неудача с конфигурацией не повод пропускать команды.
		slog.Warn("конфигурация не обновлена", "error", err)
	}

	a.flushPending(ctx)

	for _, command := range response.Commands {
		result := a.dispatcher.Execute(ctx, command)
		if err := a.options.Client.CommandResult(ctx, command.ID, result); err != nil {
			slog.Warn("результат отложен", "command_id", command.ID, "error", err)
			a.enqueue(pendingResult{commandID: command.ID, body: result})
		}
	}

	interval := time.Duration(response.HeartbeatIntervalSeconds) * time.Second
	if interval <= 0 {
		interval = 30 * time.Second
	}
	return JitterInterval(interval, a.random), nil
}

// Run крутит цикл до отмены контекста.
func (a *Agent) Run(ctx context.Context) error {
	for {
		pause, err := a.RunOnce(ctx)
		if errors.Is(err, ErrRevoked) {
			return err
		}
		if err != nil {
			slog.Error("проход цикла не удался", "error", err, "retry_in", pause)
		}

		select {
		case <-ctx.Done():
			return nil
		case <-time.After(pause):
		}
	}
}

// MaybeRenew продлевает сертификат, если истекло 2/3 его срока.
//
// Новая ключевая пара обязательна: переиспользование старого ключа означало бы,
// что однажды случившаяся компрометация переживает все продления.
func (a *Agent) MaybeRenew(ctx context.Context, leaf *x509.Certificate) error {
	a.certNotAfter = leaf.NotAfter
	if !keystore.RenewalDue(leaf, a.options.Now()) {
		return nil
	}

	key, err := keystore.GenerateKey()
	if err != nil {
		return err
	}
	csrPEM, err := keystore.CreateCSR(key)
	if err != nil {
		return err
	}
	response, err := a.options.Client.Renew(ctx, csrPEM)
	if err != nil {
		return err
	}
	keyPEM, err := keystore.EncodeKey(key)
	if err != nil {
		return err
	}

	// Запись только после успешного ответа: неудача продления оставляет
	// действующий сертификат нетронутым, и у агента есть 30 суток запаса.
	if err := keystore.Save(a.options.Layout, a.options.Guard, keyPEM, []byte(response.CertificatePEM)); err != nil {
		return err
	}
	slog.Info("сертификат продлён", "not_after", response.NotAfter)
	return nil
}
```

- [x] **Шаг 5: Запустить тесты**

```bash
go test ./internal/runner/ -v
```

Expected: PASS, 19 тестов (задачи 7, 8 и 9).

- [x] **Шаг 6: Зафиксировать**

```bash
cd .. && git add agent/internal/runner
git commit -m "feat: agent heartbeat loop with command execution and renewal"
```

---

## Задача 10: Подкоманды и точка входа

**Files:**
- Create: `agent/cmd/barysguard-agent/main.go`
- Create: `agent/cmd/barysguard-agent/enroll.go`
- Test: `agent/cmd/barysguard-agent/enroll_test.go`

**Interfaces:**
- Consumes: всё предыдущее.
- Produces: бинарь `barysguard-agent` с подкомандами `enroll`, `run`, `status`; `runEnroll(opts enrollOptions) error`.

- [x] **Шаг 1: Написать падающий тест регистрации**

Создать `agent/cmd/barysguard-agent/enroll_test.go`:

```go
package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/transport"
)

func TestEnrollStoresIdentityAndRefusesToRepeat(t *testing.T) {
	certificatePEM := selfSignedPEM(t)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request transport.EnrollRequest
		json.NewDecoder(r.Body).Decode(&request)
		if request.CSRPEM == "" {
			t.Error("CSR не прислан")
		}
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(transport.EnrollResponse{
			AgentID:                  "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f",
			CertificatePEM:           string(certificatePEM),
			CAPEM:                    string(certificatePEM),
			ConfigVersion:            12,
			HeartbeatIntervalSeconds: 30,
		})
	}))
	t.Cleanup(server.Close)

	dir := t.TempDir()
	options := enrollOptions{
		dataDir:   dir,
		serverURL: server.URL,
		token:     "BG-ENROLL-AAAA",
		caFile:    writeTempCA(t, dir, certificatePEM),
	}

	if err := runEnroll(options); err != nil {
		t.Fatalf("runEnroll: %v", err)
	}

	layout := config.NewLayout(dir)
	state, err := config.LoadState(layout)
	if err != nil {
		t.Fatalf("LoadState: %v", err)
	}
	if state.AgentID != "6f1a9c2e-0e4b-4f9c-9a3e-1d2b3c4d5e6f" {
		t.Fatalf("agent_id = %q", state.AgentID)
	}
	if _, err := os.Stat(layout.KeyPath()); err != nil {
		t.Fatalf("ключ не сохранён: %v", err)
	}

	// Затереть действующую личность одной неосторожной командой нельзя.
	if err := runEnroll(options); err == nil {
		t.Fatal("повторная регистрация без --force обязана отказывать")
	}
}

func TestEnrollDemandsTrustAnchor(t *testing.T) {
	// Ни файла CA, ни отпечатка — доверять нечему, и выбирать
	// что-нибудь на своё усмотрение агент не вправе.
	err := runEnroll(enrollOptions{
		dataDir:   t.TempDir(),
		serverURL: "https://example.invalid",
		token:     "BG-ENROLL-AAAA",
	})
	if err == nil {
		t.Fatal("регистрация без якоря доверия обязана отказывать")
	}
}

func writeTempCA(t *testing.T, dir string, caPEM []byte) string {
	t.Helper()
	path := filepath.Join(dir, "bundled-ca.crt")
	if err := os.WriteFile(path, caPEM, 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	return path
}

// selfSignedPEM выпускает сертификат прямо в тесте.
//
// Готовая константа в файле устарела бы молча: срок действия истекает,
// и тест начинает падать через год по причине, не связанной с кодом.
func selfSignedPEM(t *testing.T) []byte {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("ключ: %v", err)
	}
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "BarysGuard Test CA"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign,
		BasicConstraintsValid: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatalf("сертификат: %v", err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
}
```

Импорты файла: `crypto/ecdsa`, `crypto/elliptic`, `crypto/rand`, `crypto/x509`, `crypto/x509/pkix`, `encoding/json`, `encoding/pem`, `math/big`, `net/http`, `net/http/httptest`, `os`, `path/filepath`, `testing`, `time`, плюс пакеты `config` и `transport` агента.

- [x] **Шаг 2: Запустить тест и убедиться, что он падает**

```bash
go test ./cmd/barysguard-agent/ -v
```

Expected: FAIL — `undefined: runEnroll`.

- [x] **Шаг 3: Создать `agent/cmd/barysguard-agent/enroll.go`**

```go
package main

import (
	"context"
	"errors"
	"fmt"
	"os"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/hostfacts"
	"github.com/barysguard/agent/internal/keystore"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/transport"
)

type enrollOptions struct {
	dataDir   string
	serverURL string
	token     string
	caFile    string
	caPin     string
	force     bool
}

// trustAnchor добывает CA, которому агент будет доверять.
//
// Молчаливого доверия к тому, что пришло по сети, здесь нет: либо файл
// из дистрибутива, либо отпечаток, с которым сверяется загруженное.
func trustAnchor(ctx context.Context, options enrollOptions) ([]byte, error) {
	if options.caFile != "" {
		return os.ReadFile(options.caFile)
	}
	if options.caPin != "" {
		return transport.FetchCA(ctx, options.serverURL, options.caPin)
	}
	return nil, errors.New("нужен --ca-file из дистрибутива либо --ca-pin с отпечатком CA")
}

func runEnroll(options enrollOptions) error {
	ctx := context.Background()
	layout := config.NewLayout(options.dataDir)
	guard := platform.New()

	if !options.force {
		if _, err := os.Stat(layout.CertPath()); err == nil {
			return fmt.Errorf("агент уже зарегистрирован: %s существует (--force перезапишет)", layout.CertPath())
		}
	}

	caPEM, err := trustAnchor(ctx, options)
	if err != nil {
		return fmt.Errorf("якорь доверия: %w", err)
	}
	if err := keystore.SaveCA(layout, guard, caPEM); err != nil {
		return err
	}
	pool, err := keystore.LoadCAPool(layout)
	if err != nil {
		return err
	}

	key, err := keystore.GenerateKey()
	if err != nil {
		return err
	}
	csrPEM, err := keystore.CreateCSR(key)
	if err != nil {
		return err
	}
	facts, err := hostfacts.Collect(guard, agentVersion)
	if err != nil {
		return err
	}

	client, err := transport.NewBootstrap(options.serverURL, pool)
	if err != nil {
		return err
	}
	response, err := client.Enroll(ctx, transport.EnrollRequest{
		Token:  options.token,
		CSRPEM: csrPEM,
		Host:   facts,
	})
	if err != nil {
		return fmt.Errorf("регистрация: %w", err)
	}

	keyPEM, err := keystore.EncodeKey(key)
	if err != nil {
		return err
	}
	if err := keystore.Save(layout, guard, keyPEM, []byte(response.CertificatePEM)); err != nil {
		return err
	}
	// CA из ответа заменяет бутстрапный: сервер мог отдать полную цепочку.
	if response.CAPEM != "" {
		if err := keystore.SaveCA(layout, guard, []byte(response.CAPEM)); err != nil {
			return err
		}
	}

	if err := config.SaveSettings(layout, config.Settings{
		ServerURL: options.serverURL,
		LogLevel:  "info",
	}, guard); err != nil {
		return err
	}
	if err := config.SaveState(layout, config.State{
		AgentID:       response.AgentID,
		ConfigVersion: response.ConfigVersion,
	}, guard); err != nil {
		return err
	}

	fmt.Printf("агент зарегистрирован: %s\n", response.AgentID)
	return nil
}
```

- [x] **Шаг 4: Создать `agent/cmd/barysguard-agent/main.go`**

```go
// Command barysguard-agent — транспортное ядро агента BarysGuard.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/barysguard/agent/internal/config"
	"github.com/barysguard/agent/internal/keystore"
	"github.com/barysguard/agent/internal/platform"
	"github.com/barysguard/agent/internal/runner"
	"github.com/barysguard/agent/internal/transport"
)

// agentVersion подставляется при сборке: -ldflags "-X main.agentVersion=1.2.3".
var agentVersion = "0.1.0"

// Отдельный код для отзыва нужен супервизору: systemd с
// RestartPreventExitStatus=2 не станет бесконечно поднимать агента,
// чей сертификат отозвали намеренно.
const exitRevoked = 2

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))

	if len(os.Args) < 2 {
		usage()
		os.Exit(1)
	}

	var err error
	switch os.Args[1] {
	case "enroll":
		err = commandEnroll(os.Args[2:])
	case "run":
		err = commandRun(os.Args[2:])
	case "status":
		err = commandStatus(os.Args[2:])
	default:
		usage()
		os.Exit(1)
	}

	if errors.Is(err, runner.ErrRevoked) {
		slog.Error("обслуживание прекращено сервером", "error", err)
		os.Exit(exitRevoked)
	}
	if err != nil {
		slog.Error("выполнение не удалось", "error", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintf(os.Stderr, `barysguard-agent %s

  enroll  -token <токен> -server <URL> (-ca-file <путь> | -ca-pin <sha256>)
  run     [-data-dir <путь>]
  status  [-data-dir <путь>]
`, agentVersion)
}

func commandEnroll(args []string) error {
	flags := flag.NewFlagSet("enroll", flag.ExitOnError)
	options := enrollOptions{}
	flags.StringVar(&options.dataDir, "data-dir", config.DefaultDir(), "рабочий каталог агента")
	flags.StringVar(&options.serverURL, "server", "", "адрес сервера, например https://dlp.example:8443")
	flags.StringVar(&options.token, "token", "", "одноразовый токен регистрации")
	flags.StringVar(&options.caFile, "ca-file", "", "сертификат CA из дистрибутива")
	flags.StringVar(&options.caPin, "ca-pin", "", "отпечаток SHA-256 сертификата CA")
	flags.BoolVar(&options.force, "force", false, "перезаписать существующую регистрацию")
	if err := flags.Parse(args); err != nil {
		return err
	}

	if options.serverURL == "" || options.token == "" {
		flags.Usage()
		return errors.New("-server и -token обязательны")
	}
	return runEnroll(options)
}

// loadAgent собирает агента из того, что лежит на диске.
func loadAgent(dataDir string) (*runner.Agent, *config.Layout, error) {
	layout := config.NewLayout(dataDir)
	guard := platform.New()

	settings, err := config.LoadSettings(layout)
	if err != nil {
		return nil, nil, fmt.Errorf("настройки: %w (агент зарегистрирован?)", err)
	}
	pool, err := keystore.LoadCAPool(layout)
	if err != nil {
		return nil, nil, err
	}
	pair, _, err := keystore.Load(layout, guard)
	if err != nil {
		return nil, nil, err
	}
	client, err := transport.NewMutual(settings.ServerURL, pool, pair)
	if err != nil {
		return nil, nil, err
	}

	agent, err := runner.New(runner.Options{
		ServerURL:    settings.ServerURL,
		AgentVersion: agentVersion,
		Layout:       layout,
		Guard:        guard,
		Client:       client,
	})
	return agent, &layout, err
}

func commandRun(args []string) error {
	flags := flag.NewFlagSet("run", flag.ExitOnError)
	dataDir := flags.String("data-dir", config.DefaultDir(), "рабочий каталог агента")
	if err := flags.Parse(args); err != nil {
		return err
	}

	agent, layout, err := loadAgent(*dataDir)
	if err != nil {
		return err
	}

	// Сигнал завершения обязан останавливать цикл, а не обрывать его
	// посреди отправки результата команды.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if _, leaf, err := keystore.Load(*layout, platform.New()); err == nil {
		if err := agent.MaybeRenew(ctx, leaf); err != nil {
			slog.Warn("продление сертификата не удалось", "error", err)
		}
	}

	slog.Info("агент запущен", "version", agentVersion, "data_dir", *dataDir)
	return agent.Run(ctx)
}

func commandStatus(args []string) error {
	flags := flag.NewFlagSet("status", flag.ExitOnError)
	dataDir := flags.String("data-dir", config.DefaultDir(), "рабочий каталог агента")
	if err := flags.Parse(args); err != nil {
		return err
	}

	layout := config.NewLayout(*dataDir)
	state, err := config.LoadState(layout)
	if err != nil {
		return err
	}
	if state.AgentID == "" {
		return errors.New("агент не зарегистрирован")
	}

	_, leaf, err := keystore.Load(layout, platform.New())
	if err != nil {
		return err
	}

	fmt.Printf("agent_id:       %s\n", state.AgentID)
	fmt.Printf("config_version: %d\n", state.ConfigVersion)
	fmt.Printf("cert_not_after: %s\n", leaf.NotAfter.Format("2006-01-02 15:04:05 MST"))
	fmt.Printf("renewal_due:    %t\n", keystore.RenewalDue(leaf, timeNow()))
	return nil
}
```

Дописать в конец файла:

```go
func timeNow() time.Time { return time.Now() }
```

и добавить `"time"` в импорты.

- [x] **Шаг 5: Запустить тесты и проверить обе сборки**

```bash
go vet ./... && go test ./... -v
GOOS=linux   go build -o /dev/null ./cmd/barysguard-agent
GOOS=windows go build -o /dev/null ./cmd/barysguard-agent
```

Expected: PASS по всем пакетам; обе сборки без ошибок.

- [x] **Шаг 6: Зафиксировать**

```bash
cd .. && git add agent/cmd
git commit -m "feat: agent cli with enroll, run and status"
```

---

## Задача 11: Сквозной тест против настоящего сервера

Последняя проверка: реальный бинарь против реального FastAPI. Мок, принимающий любой запрос, подтвердил бы работоспособность неработающего агента — эта задача закрывает такую возможность.

**Files:**
- Create: `agent/e2e/e2e_test.go`
- Modify: `docs/QUICKSTART.md`

**Interfaces:**
- Consumes: бинарь `barysguard-agent`, запущенный сервер.
- Produces: тест под build tag `e2e`.

- [x] **Шаг 1: Убедиться, что Docker поднят**

```bash
docker version
```

Если демон не запущен, запустить Docker Desktop и повторить. Без него сервер не поднимется, и задача блокируется.

- [x] **Шаг 2: Поднять сервер и создать токен**

```bash
docker compose -f deploy/docker-compose.dev.yml up -d

cd server
export BG_DATABASE_URL="postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard"
export BG_CA_DIR="$PWD/.local/pki"
export BG_CA_PASSPHRASE="смените-это-значение"

.venv/Scripts/alembic.exe upgrade head
.venv/Scripts/barysguard-admin.exe create-user --username admin --role admin
.venv/Scripts/uvicorn.exe barysguard.main:app --port 8000 &
```

Ключ API печатается один раз — сохранить его в `BG_ADMIN_KEY`.

**Важно:** стенд поднимает только PostgreSQL, uvicorn слушает открытый HTTP.
Личность агента сервер берёт из заголовков `X-Client-*`, которые проставляет
nginx после проверки клиентского сертификата. На таком стенде проходит
регистрация, но не heartbeat: для него нужен обратный прокси из
`deploy/nginx/barysguard.conf`.

- [x] **Шаг 3: Написать сквозной тест**

Создать `agent/e2e/e2e_test.go`:

```go
//go:build e2e

// Сквозной тест против настоящего сервера. Под тегом, потому что требует
// поднятого Docker и запущенного FastAPI: делать его обязательным для
// каждого прогона значило бы останавливать разработку всякий раз,
// когда Docker не поднят.
//
// Запуск:
//   go test -tags e2e ./e2e/ -v \
//     -server https://127.0.0.1:8443 -token BG-ENROLL-... -ca /path/ca.crt
package e2e

import (
	"flag"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

var (
	serverURL = flag.String("server", "", "адрес шлюза")
	token     = flag.String("token", "", "токен регистрации")
	caPath    = flag.String("ca", "", "сертификат CA")
)

func buildAgent(t *testing.T) string {
	t.Helper()
	binary := filepath.Join(t.TempDir(), "barysguard-agent")
	build := exec.Command("go", "build", "-o", binary, "../cmd/barysguard-agent")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("сборка агента: %v\n%s", err, output)
	}
	return binary
}

func TestAgentEnrollsAndReportsStatus(t *testing.T) {
	if *serverURL == "" || *token == "" || *caPath == "" {
		t.Skip("нужны -server, -token и -ca")
	}

	binary := buildAgent(t)
	dataDir := t.TempDir()

	enroll := exec.Command(binary, "enroll",
		"-data-dir", dataDir, "-server", *serverURL,
		"-token", *token, "-ca-file", *caPath,
	)
	output, err := enroll.CombinedOutput()
	if err != nil {
		t.Fatalf("enroll: %v\n%s", err, output)
	}
	if !strings.Contains(string(output), "зарегистрирован") {
		t.Fatalf("неожиданный вывод: %s", output)
	}

	// Ключ обязан лежать на диске и быть закрытым от посторонних.
	if _, err := os.Stat(filepath.Join(dataDir, "pki", "agent.key")); err != nil {
		t.Fatalf("ключ не сохранён: %v", err)
	}

	status := exec.Command(binary, "status", "-data-dir", dataDir)
	output, err = status.CombinedOutput()
	if err != nil {
		t.Fatalf("status: %v\n%s", err, output)
	}
	for _, expected := range []string{"agent_id:", "config_version:", "cert_not_after:"} {
		if !strings.Contains(string(output), expected) {
			t.Errorf("в выводе status нет %q:\n%s", expected, output)
		}
	}
}
```

- [x] **Шаг 4: Прогнать сквозной тест**

Создать токен через операторский API, затем:

```bash
cd agent
go test -tags e2e ./e2e/ -v \
  -server https://127.0.0.1:8443 \
  -token "BG-ENROLL-..." \
  -ca /var/lib/barysguard/pki/ca.crt
```

Expected: PASS. Проверить в операторском API, что агент появился в списке:
`GET /api/v1/agents` обязан вернуть его с непустым `last_heartbeat_at`
после запуска `barysguard-agent run`.

- [x] **Шаг 5: Дописать раздел в `docs/QUICKSTART.md`**

Добавить в конец файла:

````markdown
## Подключение агента

Собрать агента и зарегистрировать его по токену:

```bash
cd agent
go build -o barysguard-agent ./cmd/barysguard-agent

./barysguard-agent enroll \
  -server https://dlp.example:8443 \
  -token "BG-ENROLL-..." \
  -ca-file /path/to/ca.crt \
  -data-dir ./agent-data
```

CA берётся из дистрибутива. Если его нет под рукой, допустим отпечаток:

```bash
./barysguard-agent enroll -server https://dlp.example:8443 \
  -token "BG-ENROLL-..." -ca-pin "<sha256 сертификата CA>"
```

Запуск цикла и проверка состояния:

```bash
./barysguard-agent run    -data-dir ./agent-data
./barysguard-agent status -data-dir ./agent-data
```
````

- [x] **Шаг 6: Зафиксировать**

```bash
cd .. && git add agent/e2e docs/QUICKSTART.md
git commit -m "test: end-to-end check of the agent against a live server"
```

---

## Проверка готовности плана

После задачи 11 пройти по критериям спеки:

```bash
cd agent
go vet ./... && go test ./... && \
  GOOS=linux go build ./... && GOOS=windows go build ./...
```

| Критерий | Чем подтверждается |
|---|---|
| Сборка под обе платформы | Команда выше |
| Регистрация по токену | Задача 11, шаг 4 |
| Heartbeat обновляет запись агента | `GET /api/v1/agents`, непустой `last_heartbeat_at` |
| Команда доезжает и исполняется | Поставить `ping` через `POST /api/v1/agents/{id}/commands`, проверить результат в `GET .../commands` |
| Новая конфигурация забирается | Изменить `PUT /api/v1/config`, убедиться, что `config_version` в `state.json` сменилась |
| Совпавшая версия даёт `304` | Задача 9, `TestConfigIsFetchedWhenVersionDiffers` |
| Отзыв прекращает обслуживание | Отозвать сертификат, убедиться, что агент вышел с кодом 2 |
| Обрыв связи не роняет агента | Задача 9, `TestServerErrorProducesBackoffNotFailure` |

## Что остаётся за пределами этого плана

| Что | Где |
|---|---|
| Offline-буфер на bbolt, `POST /events` | План 1C |
| Долговечная очередь результатов команд | План 1C, вместе с буфером |
| DPAPI для ключа на Windows | Отдельная задача; точка расширения — `platform.Guard` |
| Сборщики событий: ФС, буфер обмена, печать | Подпроект 2 |
| Установка службой ОС, MSI, deb/rpm | Подпроект 5 |
| Боевые команды: изоляция хоста, завершение процесса | Подпроект 2 |
