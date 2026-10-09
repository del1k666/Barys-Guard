# Network Upload Detection (N1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Агент на Windows находит отправку документа во внешний сервис (облако, мессенджер, веб-почта) по связке «процесс прочитал файл и отправил на адрес сервиса не меньше байт, чем размер файла», снимает зашифрованную копию и шлёт событие `network/upload`; воркер и правила превращают его в инцидент.

**Architecture:** Новый сборщик `netupload` (пакет `agent/internal/collectors/netupload`). Платформенно-независимое ядро — каталог сервисов, кэш DNS, окно прочитанных файлов, решающий узел `Matcher` и сам сборщик — работает над интерфейсом `Source`. Windows-реализация `Source` читает три ETW-провайдера (Kernel-File, Kernel-Network, DNS-Client). Копия снимается существующими `filewatch.Hasher` и `artifacts.Stager`; на сервере добавляется только проверка `subject` канала `network`, модель конфигурации и подписи в консоли.

**Tech Stack:** Go 1.23 (`golang.org/x/sys/windows`, `github.com/bi-zone/etw` для ETW), Python 3.13 + FastAPI + pydantic (сервер), React + TypeScript (консоль).

**Spec:** `docs/superpowers/specs/2026-10-09-network-upload-n1-design.md` (основа: `2026-10-05-collectors-files-usb-design.md`, `2026-10-05-artifact-upload-design.md`)

## Global Constraints

- Серверные команды из `server/`: `BG_TEST_DATABASE_URL="postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard" .venv/Scripts/python -m pytest <путь> -q`; перед коммитом серверной задачи: `.venv/Scripts/python -m ruff check .`, `.venv/Scripts/python -m ruff format --check .`, `.venv/Scripts/python -m mypy barysguard`. Агент — из `agent/`: `go test ./...`, `go vet ./...`, `GOOS=windows go vet ./...`, `GOOS=linux go build ./...`. Консоль — из `web/`: `npx vitest run`, `npm run typecheck`, `npm run build`.
- Строки интерфейса, комментарии и докстринги — на русском, как в остальном коде; идентификаторы — английские. Строки консоли только в `web/src/i18n/ru.ts`. Длина строки Python 100.
- Файлы пишутся с LF (инструменты Write/Edit); не переписывать файлы через Python в текстовом режиме на Windows.
- Ключ раздела конфигурации агента — `collectors.net_upload` (в стиле `file_watch`; в спеке написано `netupload` — отклонение принято, спека правится в Task 7). Пустой список `extensions`, `exclude_paths` или `services` в документе означает «встроенное значение агента» (каталог живёт в одном месте — в Go).
- Событие: канал `network`, действие `upload`, критичность `high`. Обязательные поля `subject`: `src_path` (непустая строка), `volume` (объект с `type`). Необязательные: `size_bytes`, `sent_bytes` (неотрицательные целые, не bool), `service`, `service_name`, `dest_host`, `confidence` (`high`|`medium`).
- Формула сопоставления: файл засчитывается, если накопленный объём отправки процессом на адрес одного сервиса ≥ `size * (100 - size_tolerance_percent) / 100`; `confidence = "high"`, если объём ≥ размера файла, иначе `"medium"`. Окно — `window_seconds` (60), допуск — 20 %, минимальный размер — 1024, максимальный — `collectors.artifact.max_bytes`.
- Сборщик не должен падать и не должен мешать остальным: нет прав/ETW → событие `agent/netupload_unavailable` и штатное завершение `Run` (возврат `nil`); смена конфигурации перезапускает группу сборщиков, поэтому `Run` обязан вернуться сразу после отмены контекста.
- Приватность: на сервер уходят путь, сервис, домен из каталога, размеры; содержимое трафика не читается. Копия файла — только зашифрованный артефакт через `artifacts.Stager`.
- Коммиты оканчиваются строкой `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; `prompt.txt` не коммитить.

## Review Focus

1. **Нет прав / ETW недоступен.** Сборщик шлёт `agent/netupload_unavailable` и не роняет группу (Task 5, Task 6).
2. **Бесконтрольный рост памяти.** Миллионы событий чтения не растят окна и таблицу файловых объектов (Task 3, Task 6).
3. **Загрузка кусками.** Один файл, отправленный сотней пакетов, даёт одно событие, а не сотню (Task 3).
4. **Файл исчез или занят до снятия копии.** Событие без артефакта с понятной причиной или без события; без паники (Task 5).
5. **Похожий домен и IPv4-in-IPv6.** `notdropbox.com` и `dropbox.com.evil.io` не засчитываются как Dropbox; адрес `::ffff:149.154.167.1` совпадает с диапазоном Telegram (Task 2).

---

## File Structure

Создаются (Go, `agent/internal/collectors/netupload/`):
- `config.go`, `config_test.go` — конфигурация и разбор документа
- `catalog.go`, `catalog_test.go` — каталог сервисов (домены, диапазоны), встроенный список
- `filter.go`, `filter_test.go` — расширения и исключения путей
- `dnsmap.go`, `dnsmap_test.go` — кэш «адрес → имена», разбор ответа DNS, `Resolver`
- `reads.go`, `reads_test.go` — окно прочитанных файлов по процессам
- `matcher.go`, `matcher_test.go` — решающий узел
- `source.go` — `Event`, `Kind`, `Source`
- `build.go`, `build_test.go` — конверт события
- `collector.go`, `collector_test.go` — сборщик
- `process_windows.go`, `process_other.go` — `ProcessInfo(pid)`
- `ntpath.go`, `ntpath_test.go` — перевод NT-путей в DOS (чистая функция)
- `source_other.go` — `NewSource()` без реализации
- `source_windows.go`, `ids_windows.go`, `dosdevices_windows.go` — ETW
- `agent/cmd/etwprobe/main_windows.go` — пробник провайдеров

Создаются (прочее): `docs/NETWORK_UPLOAD.md`.

Меняются: `agent/internal/collectors/factory.go` (+`factory_test.go`), `agent/go.mod`, `agent/go.sum`, `server/barysguard/services/event_subjects.py`, `server/barysguard/services/config.py`, `server/barysguard/services/inspection/incidents.py`, `server/tests/test_event_subjects.py`, `server/tests/test_collectors_config.py`, `server/tests/test_inspection_incidents.py`, `api/gateway-v1.yaml`, `web/src/api/schema.d.ts`, `web/src/lib/eventSummary.ts`, `web/src/lib/eventSummary.test.ts`, `docs/superpowers/specs/2026-10-09-network-upload-n1-design.md`.

---

### Task 1: Сервер и консоль — событие `network/upload` и раздел конфигурации

**Files:**
- Modify: `server/barysguard/services/event_subjects.py`, `server/barysguard/services/config.py`, `server/barysguard/services/inspection/incidents.py`
- Modify: `server/tests/test_event_subjects.py`, `server/tests/test_collectors_config.py`, `server/tests/test_inspection_incidents.py`
- Modify: `api/gateway-v1.yaml`, `web/src/api/schema.d.ts` (перегенерация)
- Modify: `web/src/lib/eventSummary.ts`, `web/src/lib/eventSummary.test.ts`

**Interfaces:**
- Produces: `subject_is_valid(Channel.NETWORK, "upload", subject)` по правилам из Global Constraints; остальные действия канала `network` по-прежнему допустимы; модель `NetUploadCollectorConfig` в `CollectorsConfig.net_upload`; заголовок инцидента для действия `upload`: «Отправка файла в сеть».

- [ ] **Step 1: Падающие тесты сервера**

В `server/tests/test_event_subjects.py` в конец файла:

```python
NET_SUBJECT = {"src_path": "C:\\Users\\u\\Documents\\plan.pdf", "volume": {"type": "fixed"}}


@pytest.mark.parametrize(
    "subject",
    [
        NET_SUBJECT,
        {**NET_SUBJECT, "size_bytes": 10, "sent_bytes": 12, "service": "gdrive"},
        {**NET_SUBJECT, "service_name": "Google Drive", "dest_host": "drive.google.com"},
    ],
)
def test_valid_network_upload_subjects(subject: dict) -> None:
    assert subject_is_valid(Channel.NETWORK, "upload", subject)


@pytest.mark.parametrize(
    "subject",
    [
        {"volume": {"type": "fixed"}},
        {**NET_SUBJECT, "src_path": ""},
        {**NET_SUBJECT, "volume": "fixed"},
        {**NET_SUBJECT, "volume": {}},
        {**NET_SUBJECT, "size_bytes": -1},
        {**NET_SUBJECT, "sent_bytes": True},
        {**NET_SUBJECT, "sent_bytes": "12"},
    ],
)
def test_invalid_network_upload_subjects(subject: dict) -> None:
    assert not subject_is_valid(Channel.NETWORK, "upload", subject)


def test_other_network_actions_are_not_validated() -> None:
    assert subject_is_valid(Channel.NETWORK, "connect", {})
```

В `server/tests/test_collectors_config.py` в конец:

```python
def test_net_upload_defaults() -> None:
    section = AgentConfigDocument().model_dump(mode="json")["collectors"]["net_upload"]

    assert section == {
        "enabled": True,
        "window_seconds": 60,
        "size_tolerance_percent": 20,
        "min_file_bytes": 1024,
        "extensions": [],
        "exclude_paths": [],
        "services": [],
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"window_seconds": 4},
        {"window_seconds": 601},
        {"size_tolerance_percent": -1},
        {"size_tolerance_percent": 91},
        {"min_file_bytes": 0},
        {"extensions": [""]},
        {"exclude_paths": [""]},
        {"services": [{"key": "Bad Key", "name": "x"}]},
        {"services": [{"key": "ok", "name": ""}]},
        {"services": [{"key": "ok", "name": "x", "cidrs": ["not-a-network"]}]},
        {"services": [{"key": "ok", "name": "x", "domains": [""]}]},
        {"services": [{"key": "ok", "name": "x", "typo": 1}]},
        {"typo_field": 1},
    ],
)
def test_net_upload_rejects_bad_values(patch: dict) -> None:
    with pytest.raises(ValidationError):
        AgentConfigDocument.model_validate({"collectors": {"net_upload": patch}})


def test_net_upload_accepts_a_custom_service() -> None:
    document = AgentConfigDocument.model_validate(
        {
            "collectors": {
                "net_upload": {
                    "extensions": ["pdf", "docx"],
                    "services": [
                        {
                            "key": "corp_cloud",
                            "name": "Корпоративное облако",
                            "domains": ["cloud.example.kz"],
                            "cidrs": ["10.20.0.0/16"],
                        }
                    ],
                }
            }
        }
    )

    assert document.collectors.net_upload.services[0].key == "corp_cloud"
```

В `server/tests/test_inspection_incidents.py` в конец:

```python
def test_upload_title() -> None:
    assert build_title("upload", MATCHES) == "Отправка файла в сеть: ИИН/БИН ×3, гриф ×1"
```

- [ ] **Step 2: Убедиться, что падает**

Run: `cd server && BG_TEST_DATABASE_URL="postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard" .venv/Scripts/python -m pytest tests/test_event_subjects.py tests/test_collectors_config.py tests/test_inspection_incidents.py -q`
Expected: FAIL (`net_upload` не существует; `upload` не проверяется; заголовок «Файловое событие»).

- [ ] **Step 3: Реализация сервера**

`server/barysguard/services/event_subjects.py`: после `USB_ACTIONS` добавить константу и функцию, ветку в `subject_is_valid`:

```python
NETWORK_UPLOAD_ACTION = "upload"
```

```python
def _non_negative_int(value: Any) -> bool:
    # bool в Python — подкласс int: True не должно сойти за размер.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _network_ok(action: str, subject: dict[str, Any]) -> bool:
    # Проверяется только отправка файла; остальные действия канала принимаются как есть.
    if action != NETWORK_UPLOAD_ACTION:
        return True
    if not _text(subject.get("src_path")) or not _volume_ok(subject.get("volume")):
        return False
    return all(
        subject.get(field) is None or _non_negative_int(subject[field])
        for field in ("size_bytes", "sent_bytes")
    )
```

и в `subject_is_valid` перед `return True`:

```python
    if channel is Channel.NETWORK:
        return _network_ok(action, subject)
```

`server/barysguard/services/inspection/incidents.py`: в `_ACTION_TITLES` добавить `"upload": "Отправка файла в сеть",`.

`server/barysguard/services/config.py`: сверху к импортам добавить `import ipaddress` (если нет) и `import re`; перед `class CollectorsConfig` добавить:

```python
_SERVICE_KEY = re.compile(r"^[a-z0-9_]{1,40}$")


class NetUploadServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    name: str = Field(min_length=1, max_length=80)
    domains: list[str] = Field(default_factory=list, max_length=64)
    cidrs: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("key")
    @classmethod
    def _key_shape(cls, value: str) -> str:
        if not _SERVICE_KEY.match(value):
            raise ValueError("key: латиница, цифры и _, до 40 символов")
        return value

    @field_validator("domains")
    @classmethod
    def _domains(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value or len(value) > 253:
                raise ValueError("domain must be 1..253 characters")
        return values

    @field_validator("cidrs")
    @classmethod
    def _cidrs(cls, values: list[str]) -> list[str]:
        for value in values:
            ipaddress.ip_network(value, strict=False)
        return values


class NetUploadCollectorConfig(BaseModel):
    """Отправка файлов в сеть. Пустой список означает «встроенное значение агента»."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    window_seconds: int = Field(default=60, ge=5, le=600)
    size_tolerance_percent: int = Field(default=20, ge=0, le=90)
    min_file_bytes: int = Field(default=1024, ge=1, le=1024 * 1024 * 1024)
    extensions: list[str] = Field(default_factory=list, max_length=64)
    exclude_paths: list[str] = Field(default_factory=list, max_length=64)
    services: list[NetUploadServiceConfig] = Field(default_factory=list, max_length=64)

    @field_validator("extensions", "exclude_paths")
    @classmethod
    def _non_empty_short_strings(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value or len(value) > 512:
                raise ValueError("each entry must be 1..512 characters")
        return values
```

и в `CollectorsConfig` после `artifact` строку:

```python
    net_upload: NetUploadCollectorConfig = NetUploadCollectorConfig()
```

- [ ] **Step 4: Прогон серверных тестов и контракта**

Run: `cd server && BG_TEST_DATABASE_URL=... .venv/Scripts/python -m pytest tests/test_event_subjects.py tests/test_collectors_config.py tests/test_inspection_incidents.py -q`
Expected: PASS.
Run: `.venv/Scripts/python -m pytest tests/test_openapi_contract.py -q` — Expected: FAIL (контракт устарел: схема конфигурации выросла).

- [ ] **Step 5: Перегенерировать контракт и типы консоли**

Из `server/` командой из `tests/test_openapi_contract.py`:

```
.venv/Scripts/python -c "import yaml; from barysguard.main import create_app; open('../api/gateway-v1.yaml', 'w', encoding='utf-8', newline='\n').write(yaml.safe_dump(create_app().openapi(), sort_keys=False, allow_unicode=True))"
```

Из `web/`: `npm run types`.
Run: `cd server && .venv/Scripts/python -m pytest tests/test_openapi_contract.py -q` — Expected: PASS.

- [ ] **Step 6: Консоль — строка о событии**

В `web/src/lib/eventSummary.test.ts` добавить в существующий `describe` (или рядом, в стиле файла):

```ts
describe("describeEvent: network upload", () => {
  it("shows the file and the service", () => {
    expect(
      describeEvent({
        channel: "network",
        action: "upload",
        subject: { src_path: "C:\\Docs\\plan.pdf", service_name: "Google Drive", service: "gdrive" },
      }),
    ).toBe("C:\\Docs\\plan.pdf → Google Drive");
  });

  it("falls back to the service key and to the bare path", () => {
    expect(
      describeEvent({ channel: "network", action: "upload", subject: { src_path: "a.pdf", service: "gdrive" } }),
    ).toBe("a.pdf → gdrive");
    expect(describeEvent({ channel: "network", action: "upload", subject: { src_path: "a.pdf" } })).toBe("a.pdf");
    expect(describeEvent({ channel: "network", action: "connect", subject: {} })).toBe("—");
  });
});
```

Run: `cd web && npx vitest run src/lib/eventSummary.test.ts` — Expected: FAIL.

В `web/src/lib/eventSummary.ts` перед `if (channel === "agent")` вставить:

```ts
  if (channel === "network") {
    const source = text(subject.src_path);
    if (action === "upload" && source) {
      const service = text(subject.service_name) || text(subject.service);
      return service ? `${source} → ${service}` : source;
    }
    return "—";
  }
```

Run: `npx vitest run`, `npm run typecheck`, `npm run build` — Expected: PASS.

- [ ] **Step 7: Проверки и коммит**

Run (из `server/`): полный набор pytest, `ruff check .`, `ruff format --check .`, `mypy barysguard` — Expected: чисто.

```bash
git add server api web
git commit -m "feat(server,web): network upload event, net_upload collector config and console labels

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Агент — конфигурация, каталог сервисов, фильтр файлов

**Files:**
- Create: `agent/internal/collectors/netupload/config.go`, `config_test.go`, `catalog.go`, `catalog_test.go`, `filter.go`, `filter_test.go`

**Interfaces:**
- Produces:
  - `type Service struct{ Key, Name string; Domains, CIDRs []string }`
  - `type Config struct{ Enabled bool; Window time.Duration; TolerancePercent int; MinFileBytes, MaxFileBytes int64; Extensions, ExcludePaths []string; Services []Service }`
  - `DefaultConfig() Config`, `ConfigFromDocument(document map[string]any) Config`
  - `DefaultServices() []Service`
  - `type Catalog`; `NewCatalog([]Service) *Catalog`; `(*Catalog).MatchDomain(string) (Service, bool)`; `(*Catalog).MatchAddr(netip.Addr) (Service, bool)`
  - `type Filter`; `NewFilter(Config) *Filter`; `(*Filter).PathOK(path string) bool`; `(*Filter).SizeOK(size int64) bool`

- [ ] **Step 1: Падающие тесты**

`agent/internal/collectors/netupload/config_test.go`:

```go
package netupload

import (
	"testing"
	"time"
)

func TestDefaultsWorkWithoutTheSection(t *testing.T) {
	cfg := ConfigFromDocument(map[string]any{})

	if !cfg.Enabled || cfg.Window != 60*time.Second || cfg.TolerancePercent != 20 || cfg.MinFileBytes != 1024 {
		t.Fatalf("умолчания: %+v", cfg)
	}
	if cfg.MaxFileBytes != 50*1024*1024 {
		t.Fatalf("MaxFileBytes = %d, берётся из collectors.artifact", cfg.MaxFileBytes)
	}
	if len(cfg.Extensions) == 0 || len(cfg.ExcludePaths) == 0 || len(cfg.Services) == 0 {
		t.Fatalf("встроенные списки пусты: %+v", cfg)
	}
}

func TestOverridesAreApplied(t *testing.T) {
	doc := map[string]any{"collectors": map[string]any{
		"artifact": map[string]any{"max_bytes": float64(1000)},
		"net_upload": map[string]any{
			"enabled":                false,
			"window_seconds":         float64(30),
			"size_tolerance_percent": float64(10),
			"min_file_bytes":         float64(500),
			"extensions":             []any{".PDF", "docx"},
			"exclude_paths":          []any{`*\Temp\*`},
			"services": []any{map[string]any{
				"key": "corp", "name": "Корп", "domains": []any{"cloud.example.kz"}, "cidrs": []any{"10.0.0.0/8"},
			}},
		},
	}}

	cfg := ConfigFromDocument(doc)

	if cfg.Enabled || cfg.Window != 30*time.Second || cfg.TolerancePercent != 10 || cfg.MinFileBytes != 500 {
		t.Fatalf("числа: %+v", cfg)
	}
	if cfg.MaxFileBytes != 1000 {
		t.Fatalf("MaxFileBytes = %d", cfg.MaxFileBytes)
	}
	if len(cfg.Extensions) != 2 || cfg.Extensions[0] != "pdf" || cfg.Extensions[1] != "docx" {
		t.Fatalf("расширения нормализуются: %v", cfg.Extensions)
	}
	if len(cfg.ExcludePaths) != 1 {
		t.Fatalf("исключения: %v", cfg.ExcludePaths)
	}
	if len(cfg.Services) != 1 || cfg.Services[0].Key != "corp" || cfg.Services[0].Domains[0] != "cloud.example.kz" {
		t.Fatalf("сервисы: %+v", cfg.Services)
	}
}

func TestNonsenseFallsBackToDefaults(t *testing.T) {
	doc := map[string]any{"collectors": map[string]any{"net_upload": map[string]any{
		"window_seconds":         float64(0),
		"size_tolerance_percent": float64(95),
		"min_file_bytes":         "много",
		"extensions":             []any{},
		"services":               []any{map[string]any{"name": "без ключа"}, "мусор"},
	}}}

	cfg := ConfigFromDocument(doc)

	def := DefaultConfig()
	if cfg.Window != def.Window || cfg.TolerancePercent != def.TolerancePercent || cfg.MinFileBytes != def.MinFileBytes {
		t.Fatalf("бессмысленное должно заменяться умолчанием: %+v", cfg)
	}
	if len(cfg.Services) != len(def.Services) {
		t.Fatalf("сервис без ключа не годится, берётся встроенный каталог: %d", len(cfg.Services))
	}
}
```

`agent/internal/collectors/netupload/catalog_test.go`:

```go
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
```

`agent/internal/collectors/netupload/filter_test.go`:

```go
package netupload

import "testing"

func TestFilterPath(t *testing.T) {
	f := NewFilter(DefaultConfig())

	for _, path := range []string{`C:\Users\a\Documents\План.PDF`, `E:\report.docx`, `D:\x\archive.zip`} {
		if !f.PathOK(path) {
			t.Errorf("%q должен быть кандидатом", path)
		}
	}
	for _, path := range []string{
		`C:\Users\a\Documents\photo.jpg`,
		`C:\Users\a\Documents\noext`,
		`C:\Users\a\AppData\Local\x\cache.pdf`,
		`C:\Windows\System32\help.pdf`,
		`C:\Program Files\App\readme.txt`,
		`C:\ProgramData\x\a.csv`,
		`C:\$Recycle.Bin\S-1\a.pdf`,
		`C:\Users\a\Documents\dir.v2\file`,
	} {
		if f.PathOK(path) {
			t.Errorf("%q не должен быть кандидатом", path)
		}
	}
}

func TestFilterSize(t *testing.T) {
	cfg := DefaultConfig()
	cfg.MinFileBytes = 100
	cfg.MaxFileBytes = 1000
	f := NewFilter(cfg)

	cases := map[int64]bool{99: false, 100: true, 1000: true, 1001: false, 0: false}
	for size, want := range cases {
		if got := f.SizeOK(size); got != want {
			t.Errorf("SizeOK(%d) = %v, ждали %v", size, got, want)
		}
	}
}
```

- [ ] **Step 2: Убедиться, что падает**

Run: `cd agent && go test ./internal/collectors/netupload/ -run 'Defaults|Overrides|Nonsense|MatchDomain|MatchAddr|BadCIDR|EveryDefault|Filter' -v`
Expected: FAIL — пакет без реализации (`undefined: ConfigFromDocument` и т. п.).

- [ ] **Step 3: Реализация**

`agent/internal/collectors/netupload/config.go`:

```go
// Package netupload — сборщик «отправка файла в сеть»: находит документ,
// который процесс прочитал и отправил на адрес известного сервиса, снимает
// зашифрованную копию и присылает событие network/upload.
package netupload

import (
	"strings"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
)

// Умолчания совпадают с серверными (services/config.py): агент обязан работать
// и с документом, в котором раздела collectors.net_upload нет.
const (
	defaultWindowSeconds = 60
	defaultTolerance     = 20
	defaultMinFileBytes  = 1024
	maxTolerance         = 90
)

var (
	defaultExtensions = []string{
		"pdf", "docx", "xlsx", "pptx", "doc", "xls", "ppt", "rtf", "txt", "csv", "zip", "7z", "rar",
	}
	// Системные каталоги, профили приложений и корзина: чтение оттуда — не работа пользователя с документом.
	defaultExclude = []string{
		`*\AppData\*`, `?:\Windows\*`, `?:\Program Files*`, `?:\ProgramData\*`, `*\$Recycle.Bin\*`,
	}
)

// Service — внешний сервис, куда уходят файлы. Опознаётся по домену (по ответам
// DNS) или, если домен неизвестен, по диапазону адресов.
type Service struct {
	Key, Name string
	Domains   []string
	CIDRs     []string
}

type Config struct {
	Enabled bool
	// Window — окно «прочитал → отправил».
	Window time.Duration
	// TolerancePercent — допуск на сжатие и накладные расходы.
	TolerancePercent int
	MinFileBytes     int64
	// MaxFileBytes — предел размера файла; берётся из collectors.artifact.max_bytes.
	MaxFileBytes int64
	Extensions   []string
	ExcludePaths []string
	Services     []Service
}

func DefaultConfig() Config {
	return Config{
		Enabled:          true,
		Window:           defaultWindowSeconds * time.Second,
		TolerancePercent: defaultTolerance,
		MinFileBytes:     defaultMinFileBytes,
		MaxFileBytes:     artifacts.DefaultConfig().MaxBytes,
		Extensions:       append([]string(nil), defaultExtensions...),
		ExcludePaths:     append([]string(nil), defaultExclude...),
		Services:         DefaultServices(),
	}
}

// ConfigFromDocument читает collectors.net_upload. Отсутствующее или
// бессмысленное значение заменяется умолчанием; пустой список означает
// «встроенное значение».
func ConfigFromDocument(document map[string]any) Config {
	cfg := DefaultConfig()
	cfg.MaxFileBytes = artifacts.ConfigFromDocument(document).MaxBytes

	collectors, _ := document["collectors"].(map[string]any)
	section, _ := collectors["net_upload"].(map[string]any)

	if value, ok := section["enabled"].(bool); ok {
		cfg.Enabled = value
	}
	if value, ok := section["window_seconds"].(float64); ok && value >= 1 {
		cfg.Window = time.Duration(value) * time.Second
	}
	if value, ok := section["size_tolerance_percent"].(float64); ok && value >= 0 && value <= maxTolerance {
		cfg.TolerancePercent = int(value)
	}
	if value, ok := section["min_file_bytes"].(float64); ok && value >= 1 {
		cfg.MinFileBytes = int64(value)
	}
	if list := stringList(section["extensions"]); len(list) > 0 {
		cfg.Extensions = normalizeExtensions(list)
	}
	if list := stringList(section["exclude_paths"]); len(list) > 0 {
		cfg.ExcludePaths = list
	}
	if services := servicesFrom(section["services"]); len(services) > 0 {
		cfg.Services = services
	}
	return cfg
}

func stringList(raw any) []string {
	items, _ := raw.([]any)
	var out []string
	for _, item := range items {
		if text, ok := item.(string); ok && strings.TrimSpace(text) != "" {
			out = append(out, strings.TrimSpace(text))
		}
	}
	return out
}

func normalizeExtensions(list []string) []string {
	out := make([]string, 0, len(list))
	for _, ext := range list {
		out = append(out, strings.ToLower(strings.TrimPrefix(ext, ".")))
	}
	return out
}

func servicesFrom(raw any) []Service {
	items, _ := raw.([]any)
	var out []Service
	for _, item := range items {
		entry, _ := item.(map[string]any)
		key, _ := entry["key"].(string)
		if strings.TrimSpace(key) == "" {
			continue
		}
		name, _ := entry["name"].(string)
		if name == "" {
			name = key
		}
		out = append(out, Service{
			Key: key, Name: name,
			Domains: stringList(entry["domains"]), CIDRs: stringList(entry["cidrs"]),
		})
	}
	return out
}
```

`agent/internal/collectors/netupload/catalog.go`:

```go
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
```

`agent/internal/collectors/netupload/filter.go`:

```go
package netupload

import (
	"strings"

	"github.com/barysguard/agent/internal/collectors/filewatch"
)

// Filter отбрасывает чтения, не похожие на работу с документом. Он дёшево
// отвечает по пути и размеру и не обращается к диску.
type Filter struct {
	extensions map[string]struct{}
	exclude    *filewatch.Excluder
	min, max   int64
}

func NewFilter(cfg Config) *Filter {
	extensions := make(map[string]struct{}, len(cfg.Extensions))
	for _, ext := range cfg.Extensions {
		extensions[strings.ToLower(strings.TrimPrefix(ext, "."))] = struct{}{}
	}
	return &Filter{
		extensions: extensions,
		exclude:    filewatch.NewExcluder(cfg.ExcludePaths),
		min:        cfg.MinFileBytes,
		max:        cfg.MaxFileBytes,
	}
}

// extension возвращает расширение без точки; точка после последнего разделителя
// не считается (каталог вида dir.v2).
func extension(path string) string {
	index := strings.LastIndexAny(path, `./\`)
	if index < 0 || path[index] != '.' {
		return ""
	}
	return strings.ToLower(path[index+1:])
}

func (f *Filter) PathOK(path string) bool {
	if _, ok := f.extensions[extension(path)]; !ok {
		return false
	}
	return !f.exclude.Match(path)
}

func (f *Filter) SizeOK(size int64) bool {
	return size >= f.min && size <= f.max
}
```

Примечание: в тесте `TestFilterPath` путь `C:\Users\a\Documents\dir.v2\file` не имеет расширения после последнего разделителя — `extension` вернёт пустую строку, кандидат отброшен.

- [ ] **Step 4: Тесты проходят**

Run: `cd agent && go test ./internal/collectors/netupload/ -v`
Expected: PASS.

- [ ] **Step 5: Проверки и коммит**

Run: `cd agent && go vet ./... && GOOS=windows go vet ./... && GOOS=linux go build ./...`
Expected: чисто.

```bash
git add agent/internal/collectors/netupload
git commit -m "feat(agent): net_upload config, services catalog and file filter

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Агент — кэш DNS, окно прочитанных файлов, решающий узел

**Files:**
- Create: `agent/internal/collectors/netupload/dnsmap.go`, `dnsmap_test.go`, `reads.go`, `reads_test.go`, `matcher.go`, `matcher_test.go`

**Interfaces:**
- Consumes (Task 2): `Config`, `Service`, `Catalog` (`MatchDomain`, `MatchAddr`), `normalizeHost`.
- Produces:
  - `ParseDNSResults(queryName, results string) (names []string, addrs []netip.Addr)`
  - `type DNSCache`; `NewDNSCache(max int) *DNSCache`; `(*DNSCache).Learn(names []string, addrs []netip.Addr, ttl time.Duration, now time.Time)`; `(*DNSCache).Names(addr netip.Addr, now time.Time) []string`
  - `type Resolver struct{ Catalog *Catalog; DNS *DNSCache }`; `(*Resolver).Resolve(addr netip.Addr, now time.Time) (svc Service, host string, ok bool)`
  - `type Read struct{ Path string; Size int64; At time.Time }`; `type Reads`; `NewReads(window time.Duration, maxPIDs, maxPerPID int) *Reads`; `(*Reads).Add(pid uint32, r Read)`; `(*Reads).Recent(pid uint32, now time.Time) []Read`
  - `type Send struct{ PID uint32; Addr netip.Addr; Bytes uint64; At time.Time }`; `type Match struct{ PID uint32; Read Read; Service Service; Host string; Sent uint64; Confidence string }`
  - `type Matcher`; `NewMatcher(cfg Config, reads *Reads, res *Resolver) *Matcher`; `(*Matcher).Observe(s Send) []Match`
  - константы `ConfidenceHigh = "high"`, `ConfidenceMedium = "medium"`

- [ ] **Step 1: Падающие тесты**

`agent/internal/collectors/netupload/dnsmap_test.go`:

```go
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
```

`agent/internal/collectors/netupload/reads_test.go`:

```go
package netupload

import (
	"fmt"
	"testing"
	"time"
)

func TestReadsKeepRecentAndRefreshDuplicates(t *testing.T) {
	now := time.Unix(1_000, 0)
	reads := NewReads(time.Minute, 10, 10)

	reads.Add(7, Read{Path: `C:\a.pdf`, Size: 100, At: now})
	reads.Add(7, Read{Path: `C:\a.pdf`, Size: 100, At: now.Add(30 * time.Second)})
	reads.Add(7, Read{Path: `C:\b.pdf`, Size: 200, At: now.Add(40 * time.Second)})

	if got := reads.Recent(7, now.Add(50*time.Second)); len(got) != 2 {
		t.Fatalf("повтор пути обновляет запись, а не плодит: %v", got)
	}
	// a.pdf обновлялся на 30-й секунде, b.pdf — на 40-й; на 95-й a.pdf (65 с) вышел из окна, b.pdf (55 с) остался.
	if got := reads.Recent(7, now.Add(95*time.Second)); len(got) != 1 || got[0].Path != `C:\b.pdf` {
		t.Fatalf("устаревшее не отдаётся: %v", got)
	}
	if got := reads.Recent(8, now); len(got) != 0 {
		t.Fatalf("чужой процесс: %v", got)
	}
}

func TestReadsAreBounded(t *testing.T) {
	now := time.Unix(1_000, 0)
	reads := NewReads(time.Hour, 3, 2)

	for i := 0; i < 5; i++ {
		reads.Add(1, Read{Path: fmt.Sprintf(`C:\f%d.pdf`, i), Size: 10, At: now.Add(time.Duration(i) * time.Second)})
	}
	if got := reads.Recent(1, now.Add(10*time.Second)); len(got) != 2 || got[0].Path != `C:\f3.pdf` {
		t.Fatalf("на процесс держится не больше 2 последних: %v", got)
	}

	for pid := uint32(10); pid < 20; pid++ {
		reads.Add(pid, Read{Path: `C:\x.pdf`, Size: 10, At: now.Add(time.Duration(pid) * time.Minute)})
	}
	if size := reads.Processes(); size > 3 {
		t.Fatalf("процессов %d при пределе 3", size)
	}
}
```

`agent/internal/collectors/netupload/matcher_test.go`:

```go
package netupload

import (
	"net/netip"
	"testing"
	"time"
)

var t0 = time.Unix(10_000, 0)

func newTestMatcher(t *testing.T) (*Matcher, *Reads, *Resolver) {
	t.Helper()
	cfg := DefaultConfig()
	reads := NewReads(cfg.Window, 100, 100)
	res := &Resolver{Catalog: NewCatalog(DefaultServices()), DNS: NewDNSCache(100)}
	res.DNS.Learn([]string{"content.dropboxapi.com"}, []netip.Addr{addr("162.125.1.14")}, time.Hour, t0)
	return NewMatcher(cfg, reads, res), reads, res
}

func send(pid uint32, to string, bytes uint64, at time.Time) Send {
	return Send{PID: pid, Addr: addr(to), Bytes: bytes, At: at}
}

func TestMatchWhenSentCoversTheFile(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 10_000, At: t0})

	got := m.Observe(send(5, "162.125.1.14", 10_500, t0.Add(2*time.Second)))

	if len(got) != 1 {
		t.Fatalf("ждали одно совпадение: %v", got)
	}
	match := got[0]
	if match.Read.Path != `C:\plan.pdf` || match.Service.Key != "dropbox" || match.Host != "content.dropboxapi.com" ||
		match.Confidence != ConfidenceHigh || match.Sent != 10_500 {
		t.Fatalf("совпадение: %+v", match)
	}
}

func TestMatchWithinToleranceIsMediumConfidence(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 10_000, At: t0})

	got := m.Observe(send(5, "162.125.1.14", 8_500, t0)) // 85 % — внутри допуска 20 %

	if len(got) != 1 || got[0].Confidence != ConfidenceMedium {
		t.Fatalf("%v", got)
	}
	if got := m.Observe(send(5, "162.125.1.14", 10, t0)); len(got) != 0 {
		t.Fatalf("тот же файл не должен засчитываться дважды: %v", got)
	}
}

func TestChunkedUploadProducesOneEvent(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 100_000, At: t0})

	total := 0
	for i := 0; i < 100; i++ {
		total += len(m.Observe(send(5, "162.125.1.14", 1_100, t0.Add(time.Duration(i)*100*time.Millisecond))))
	}
	if total != 1 {
		t.Fatalf("загрузка кусками дала %d событий, ждали 1", total)
	}
}

func TestNoMatchWhenTooLittleWasSent(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 100_000, At: t0})

	if got := m.Observe(send(5, "162.125.1.14", 2_000, t0)); len(got) != 0 {
		t.Fatalf("мелкий обмен не должен срабатывать: %v", got)
	}
}

func TestNoMatchForOtherProcessOrUnknownAddress(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 1_000, At: t0})

	if got := m.Observe(send(6, "162.125.1.14", 5_000, t0)); len(got) != 0 {
		t.Fatalf("другой процесс: %v", got)
	}
	if got := m.Observe(send(5, "8.8.8.8", 5_000, t0)); len(got) != 0 {
		t.Fatalf("адрес вне каталога: %v", got)
	}
}

func TestWindowExpiry(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\plan.pdf`, Size: 1_000, At: t0})

	if got := m.Observe(send(5, "162.125.1.14", 5_000, t0.Add(2*time.Minute))); len(got) != 0 {
		t.Fatalf("чтение старше окна: %v", got)
	}
}

func TestSeveralFilesShareTheSentBudget(t *testing.T) {
	m, reads, _ := newTestMatcher(t)
	reads.Add(5, Read{Path: `C:\big.pdf`, Size: 10_000, At: t0})
	reads.Add(5, Read{Path: `C:\mid.pdf`, Size: 5_000, At: t0})
	reads.Add(5, Read{Path: `C:\small.pdf`, Size: 4_000, At: t0})

	got := m.Observe(send(5, "162.125.1.14", 15_500, t0))

	// big (10 000) + mid (5 000) исчерпывают объём; на small (>= 3 200 нужно) остаётся 500.
	if len(got) != 2 || got[0].Read.Path != `C:\big.pdf` || got[1].Read.Path != `C:\mid.pdf` {
		t.Fatalf("%v", got)
	}
}

func TestInvalidToleranceDoesNotPanic(t *testing.T) {
	cfg := DefaultConfig()
	cfg.TolerancePercent = 200
	reads := NewReads(cfg.Window, 10, 10)
	res := &Resolver{Catalog: NewCatalog(DefaultServices()), DNS: NewDNSCache(10)}
	res.DNS.Learn([]string{"dropbox.com"}, []netip.Addr{addr("1.2.3.4")}, time.Hour, t0)
	m := NewMatcher(cfg, reads, res)
	reads.Add(1, Read{Path: `C:\a.pdf`, Size: 100, At: t0})

	m.Observe(send(1, "1.2.3.4", 1_000, t0))
}
```

- [ ] **Step 2: Убедиться, что падает**

Run: `cd agent && go test ./internal/collectors/netupload/ -run 'DNS|Resolver|Reads|Match|Chunked|Window|Several|Tolerance' -v`
Expected: FAIL (`undefined: ParseDNSResults` и т. д.).

- [ ] **Step 3: Реализация**

`agent/internal/collectors/netupload/dnsmap.go`:

```go
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
```

`agent/internal/collectors/netupload/reads.go`:

```go
package netupload

import (
	"sync"
	"time"
)

// Read — документ, который процесс прочитал.
type Read struct {
	Path string
	Size int64
	At   time.Time
}

type pidReads struct {
	items []Read
	last  time.Time
}

// Reads хранит для каждого процесса короткое окно последних прочитанных
// документов. Память ограничена числом процессов и записей на процесс.
type Reads struct {
	mu        sync.Mutex
	window    time.Duration
	maxPIDs   int
	maxPerPID int
	byPID     map[uint32]*pidReads
}

func NewReads(window time.Duration, maxPIDs, maxPerPID int) *Reads {
	return &Reads{window: window, maxPIDs: maxPIDs, maxPerPID: maxPerPID, byPID: map[uint32]*pidReads{}}
}

func (r *Reads) Add(pid uint32, read Read) {
	r.mu.Lock()
	defer r.mu.Unlock()
	entry := r.byPID[pid]
	if entry == nil {
		if len(r.byPID) >= r.maxPIDs {
			r.evictOldestLocked()
		}
		entry = &pidReads{}
		r.byPID[pid] = entry
	}
	entry.last = read.At
	for index := range entry.items {
		if entry.items[index].Path == read.Path {
			entry.items[index] = read
			return
		}
	}
	if len(entry.items) >= r.maxPerPID {
		entry.items = entry.items[1:]
	}
	entry.items = append(entry.items, read)
}

func (r *Reads) evictOldestLocked() {
	var oldest uint32
	var oldestAt time.Time
	first := true
	for pid, entry := range r.byPID {
		if first || entry.last.Before(oldestAt) {
			oldest, oldestAt, first = pid, entry.last, false
		}
	}
	if !first {
		delete(r.byPID, oldest)
	}
}

// Recent отдаёт чтения процесса не старше окна.
func (r *Reads) Recent(pid uint32, now time.Time) []Read {
	r.mu.Lock()
	defer r.mu.Unlock()
	entry := r.byPID[pid]
	if entry == nil {
		return nil
	}
	fresh := entry.items[:0]
	for _, read := range entry.items {
		if now.Sub(read.At) <= r.window {
			fresh = append(fresh, read)
		}
	}
	entry.items = fresh
	return append([]Read(nil), fresh...)
}

func (r *Reads) Processes() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.byPID)
}
```

`agent/internal/collectors/netupload/matcher.go`:

```go
package netupload

import (
	"net/netip"
	"sort"
	"sync"
	"time"
)

const (
	ConfidenceHigh   = "high"
	ConfidenceMedium = "medium"

	pruneAbove = 4096
)

// Send — отправка данных процессом на адрес.
type Send struct {
	PID   uint32
	Addr  netip.Addr
	Bytes uint64
	At    time.Time
}

// Match — найденная отправка файла.
type Match struct {
	PID        uint32
	Read       Read
	Service    Service
	Host       string
	Sent       uint64
	Confidence string
}

type accKey struct {
	pid     uint32
	service string
}

type reportedKey struct {
	pid           uint32
	path, service string
}

type accumulator struct {
	start     time.Time
	sent      uint64
	used      uint64
}

// Matcher решает, что процесс отправил именно прочитанный им документ.
// Объём отправки копится по паре «процесс — сервис» и расходуется на файлы по
// убыванию размера; каждый файл засчитывается один раз за окно.
type Matcher struct {
	cfg   Config
	reads *Reads
	res   *Resolver

	mu       sync.Mutex
	acc      map[accKey]*accumulator
	reported map[reportedKey]time.Time
}

func NewMatcher(cfg Config, reads *Reads, res *Resolver) *Matcher {
	if cfg.TolerancePercent < 0 || cfg.TolerancePercent > maxTolerance {
		cfg.TolerancePercent = defaultTolerance
	}
	return &Matcher{
		cfg: cfg, reads: reads, res: res,
		acc: map[accKey]*accumulator{}, reported: map[reportedKey]time.Time{},
	}
}

func (m *Matcher) Observe(send Send) []Match {
	if send.Bytes == 0 {
		return nil
	}
	service, host, ok := m.res.Resolve(send.Addr, send.At)
	if !ok {
		return nil
	}

	m.mu.Lock()
	defer m.mu.Unlock()
	m.pruneLocked(send.At)

	key := accKey{pid: send.PID, service: service.Key}
	acc := m.acc[key]
	if acc == nil || send.At.Sub(acc.start) > m.cfg.Window {
		acc = &accumulator{start: send.At}
		m.acc[key] = acc
	}
	acc.sent += send.Bytes

	candidates := m.reads.Recent(send.PID, send.At)
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].Size != candidates[j].Size {
			return candidates[i].Size > candidates[j].Size
		}
		return candidates[i].Path < candidates[j].Path
	})

	var out []Match
	for _, read := range candidates {
		seenKey := reportedKey{pid: send.PID, path: read.Path, service: service.Key}
		if at, seen := m.reported[seenKey]; seen && send.At.Sub(at) <= m.cfg.Window {
			continue
		}
		size := uint64(read.Size)
		need := size * uint64(100-m.cfg.TolerancePercent) / 100
		remaining := acc.sent - acc.used
		if need == 0 || remaining < need {
			continue
		}
		confidence := ConfidenceMedium
		if remaining >= size {
			confidence = ConfidenceHigh
		}
		spent := size
		if remaining < spent {
			spent = remaining
		}
		acc.used += spent
		m.reported[seenKey] = send.At
		out = append(out, Match{
			PID: send.PID, Read: read, Service: service, Host: host,
			Sent: acc.sent, Confidence: confidence,
		})
	}
	return out
}

// pruneLocked выбрасывает давно закрытые окна, чтобы карты не росли бесконечно.
func (m *Matcher) pruneLocked(now time.Time) {
	if len(m.acc) > pruneAbove {
		for key, acc := range m.acc {
			if now.Sub(acc.start) > 2*m.cfg.Window {
				delete(m.acc, key)
			}
		}
	}
	if len(m.reported) > pruneAbove {
		for key, at := range m.reported {
			if now.Sub(at) > 2*m.cfg.Window {
				delete(m.reported, key)
			}
		}
	}
}
```

Исправить форматирование структуры `accumulator` (`gofmt`): поля `start`, `sent`, `used` без лишних пробелов — запустить `gofmt -w` на пакете.

- [ ] **Step 4: Тесты проходят**

Run: `cd agent && gofmt -l ./internal/collectors/netupload; go test ./internal/collectors/netupload/ -v`
Expected: `gofmt` ничего не выводит; PASS.

Если `TestSeveralFilesShareTheSentBudget` падает: проверить арифметику — big: need 8 000, remaining 15 500 → засчитан, remaining 5 500; mid: need 4 000, 5 500 ≥ 4 000 → засчитан, remaining 500; small: need 3 200 → нет. Ожидание теста именно такое.

- [ ] **Step 5: Проверки и коммит**

Run: `cd agent && go vet ./... && GOOS=windows go vet ./... && GOOS=linux go build ./...`

```bash
git add agent/internal/collectors/netupload
git commit -m "feat(agent): dns cache, recent reads window and upload matcher

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Агент — перевод NT-путей, интерфейс источника, событие

**Files:**
- Create: `agent/internal/collectors/netupload/source.go`, `ntpath.go`, `ntpath_test.go`, `build.go`, `build_test.go`

**Interfaces:**
- Consumes (Task 3): `Match`, `Read`.
- Produces:
  - `type Kind int` (`KindRead`, `KindSend`, `KindDNS`); `type Event struct{ Kind Kind; PID uint32; Path string; Addr netip.Addr; Bytes uint64; Names []string; Addrs []netip.Addr; TTL time.Duration; At time.Time }`
  - `type Source interface{ Run(ctx context.Context, sink func(Event)) error }`
  - `ntToDOS(path string, devices map[string]string) (string, bool)` — `devices`: ключ `\device\harddiskvolume3` (нижний регистр) → `C:`
  - `BuildEvent(m Match, hash filewatch.HashResult, vol volumes.Volume) (events.Envelope, error)`

- [ ] **Step 1: Падающие тесты**

`agent/internal/collectors/netupload/ntpath_test.go`:

```go
package netupload

import "testing"

func TestNTToDOS(t *testing.T) {
	devices := map[string]string{
		`\device\harddiskvolume3`:  `C:`,
		`\device\harddiskvolume31`: `E:`,
	}

	cases := []struct {
		in, want string
		ok       bool
	}{
		{`\Device\HarddiskVolume3\Users\a\План.pdf`, `C:\Users\a\План.pdf`, true},
		{`\DEVICE\HARDDISKVOLUME31\x.docx`, `E:\x.docx`, true},
		{`\Device\HarddiskVolume3`, `C:\`, true},
		{`\Device\HarddiskVolume33\x.pdf`, ``, false}, // том 33, а не 3
		{`\Device\Mup\server\share\a.pdf`, ``, false},
		{`C:\already\dos.pdf`, ``, false},
		{``, ``, false},
	}
	for _, tc := range cases {
		got, ok := ntToDOS(tc.in, devices)
		if got != tc.want || ok != tc.ok {
			t.Errorf("ntToDOS(%q) = %q, %v; ждали %q, %v", tc.in, got, ok, tc.want, tc.ok)
		}
	}
}
```

`agent/internal/collectors/netupload/build_test.go`:

```go
package netupload

import (
	"testing"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

func sampleMatch() Match {
	return Match{
		PID:  42,
		Read: Read{Path: `C:\Users\a\Documents\plan.pdf`, Size: 10_000, At: time.Unix(1, 0)},
		Service: Service{Key: "gdrive", Name: "Google Drive"},
		Host: "drive.google.com", Sent: 10_800, Confidence: ConfidenceHigh,
	}
}

func TestBuildEventWithArtifact(t *testing.T) {
	hash := filewatch.HashResult{
		SHA256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
		Size:   10_000, Status: filewatch.HashOK, Staged: true,
	}

	env, err := BuildEvent(sampleMatch(), hash, volumes.Volume{Type: volumes.TypeFixed, FS: "NTFS"})
	if err != nil {
		t.Fatal(err)
	}

	if env.Channel != events.ChannelNetwork || env.Action != "upload" || env.SeverityHint != events.SeverityHigh {
		t.Fatalf("канал/действие/критичность: %s/%s/%s", env.Channel, env.Action, env.SeverityHint)
	}
	want := map[string]any{
		"src_path": `C:\Users\a\Documents\plan.pdf`, "size_bytes": int64(10_000), "service": "gdrive",
		"service_name": "Google Drive", "dest_host": "drive.google.com", "sent_bytes": uint64(10_800),
		"confidence": "high",
	}
	for key, value := range want {
		if env.Subject[key] != value {
			t.Errorf("subject[%q] = %#v, ждали %#v", key, env.Subject[key], value)
		}
	}
	volume, _ := env.Subject["volume"].(map[string]any)
	if volume["type"] != volumes.TypeFixed {
		t.Errorf("volume = %v", env.Subject["volume"])
	}
	if env.Artifact == nil || env.Artifact.SHA256 != hash.SHA256 || env.Artifact.Size != 10_000 || env.Artifact.Uploaded {
		t.Errorf("artifact = %+v", env.Artifact)
	}
}

func TestBuildEventWithoutCopyKeepsTheReason(t *testing.T) {
	cases := []struct {
		name  string
		hash  filewatch.HashResult
		label string
		value any
	}{
		{"слишком большой", filewatch.HashResult{Size: 99, Status: filewatch.HashSkippedSize}, "hash", "skipped_size"},
		{"недоступен", filewatch.HashResult{Status: filewatch.HashUnavailable}, "hash", "unavailable"},
		{"лимит скорости", filewatch.HashResult{SHA256: "a", Size: 1, Status: filewatch.HashOK, StageSkip: artifacts.SkipRate}, "artifact_skipped", "rate"},
	}
	for _, tc := range cases {
		env, err := BuildEvent(sampleMatch(), tc.hash, volumes.Volume{Type: volumes.TypeUnknown})
		if err != nil {
			t.Fatal(err)
		}
		if env.Labels[tc.label] != tc.value {
			t.Errorf("%s: labels = %v", tc.name, env.Labels)
		}
		if tc.hash.Status != filewatch.HashOK && env.Artifact != nil {
			t.Errorf("%s: без хеша не должно быть artifact", tc.name)
		}
	}
}

func TestBuildEventOmitsEmptyHost(t *testing.T) {
	match := sampleMatch()
	match.Host = ""

	env, err := BuildEvent(match, filewatch.HashResult{Status: filewatch.HashUnavailable}, volumes.Volume{Type: volumes.TypeUnknown})
	if err != nil {
		t.Fatal(err)
	}
	if _, present := env.Subject["dest_host"]; present {
		t.Fatalf("пустой домен не передаётся: %v", env.Subject)
	}
	if env.Subject["size_bytes"] != int64(10_000) {
		t.Fatalf("размер берётся из чтения, если хеша нет: %v", env.Subject["size_bytes"])
	}
}
```

- [ ] **Step 2: Убедиться, что падает**

Run: `cd agent && go test ./internal/collectors/netupload/ -run 'NTToDOS|BuildEvent' -v`
Expected: FAIL (`undefined: ntToDOS`, `BuildEvent`).

- [ ] **Step 3: Реализация**

`agent/internal/collectors/netupload/source.go`:

```go
package netupload

import (
	"context"
	"net/netip"
	"time"
)

type Kind int

const (
	// KindRead — процесс начал читать файл (Path — путь в формате DOS).
	KindRead Kind = iota + 1
	// KindSend — процесс отправил данные на Addr (Bytes).
	KindSend
	// KindDNS — ответ DNS: имена Names ведут на адреса Addrs.
	KindDNS
)

// Event — событие источника. Заполнены только поля своего вида.
type Event struct {
	Kind  Kind
	PID   uint32
	Path  string
	Addr  netip.Addr
	Bytes uint64
	Names []string
	Addrs []netip.Addr
	TTL   time.Duration
	At    time.Time
}

// Source поставляет события чтения файлов, сетевых отправок и ответов DNS.
// Run работает до отмены контекста; sink не блокируется. Ошибка Run означает,
// что источник недоступен (нет прав, нет ETW): сборщик сообщит об этом событием.
type Source interface {
	Run(ctx context.Context, sink func(Event)) error
}
```

`agent/internal/collectors/netupload/ntpath.go`:

```go
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
```

`agent/internal/collectors/netupload/build.go`:

```go
package netupload

import (
	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

// BuildEvent собирает конверт network/upload. Процесс и актёра добавляет сборщик.
func BuildEvent(match Match, hash filewatch.HashResult, vol volumes.Volume) (events.Envelope, error) {
	subject := map[string]any{
		"src_path": match.Read.Path,
		"volume": map[string]any{
			"type": vol.Type, "serial": vol.Serial, "label": vol.Label, "fs": vol.FS,
		},
		"size_bytes":   match.Read.Size,
		"service":      match.Service.Key,
		"service_name": match.Service.Name,
		"sent_bytes":   match.Sent,
		"confidence":   match.Confidence,
	}
	if match.Host != "" {
		subject["dest_host"] = match.Host
	}
	labels := map[string]any{}

	env, err := events.NewEnvelope(events.ChannelNetwork, "upload", events.SeverityHigh, subject)
	if err != nil {
		return events.Envelope{}, err
	}

	switch hash.Status {
	case filewatch.HashOK:
		env.Artifact = &events.Artifact{SHA256: hash.SHA256, Size: hash.Size, Uploaded: false}
		subject["size_bytes"] = hash.Size
	case filewatch.HashSkippedSize:
		labels["hash"] = "skipped_size"
		subject["size_bytes"] = hash.Size
	case filewatch.HashUnavailable:
		labels["hash"] = "unavailable"
	}
	if hash.StageSkip == artifacts.SkipRate {
		// Бюджет копирования исчерпан: оператор видит, что содержимое не взято.
		labels["artifact_skipped"] = "rate"
	}
	env.Labels = labels
	return env, nil
}
```

- [ ] **Step 4: Тесты проходят**

Run: `cd agent && gofmt -l ./internal/collectors/netupload; go test ./internal/collectors/netupload/ -v`
Expected: PASS.

- [ ] **Step 5: Проверки и коммит**

Run: `cd agent && go vet ./... && GOOS=windows go vet ./... && GOOS=linux go build ./...`

```bash
git add agent/internal/collectors/netupload
git commit -m "feat(agent): source interface, nt path conversion and upload event builder

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Агент — сборщик и подключение к фабрике

**Files:**
- Create: `agent/internal/collectors/netupload/collector.go`, `collector_test.go`, `process_windows.go`, `process_other.go`, `source_other.go`
- Modify: `agent/internal/collectors/factory.go`, `agent/internal/collectors/factory_test.go`

**Interfaces:**
- Consumes (Tasks 2–4): `Config`, `NewFilter`, `NewDNSCache`, `NewCatalog`, `Resolver`, `NewReads`, `NewMatcher`, `Source`, `Event`, `Kind*`, `BuildEvent`; из существующего кода — `filewatch.Hasher`/`NewHasher`/`HashStaged`/`HashResult`/`VolumeFor`, `artifacts.Stager`, `identity.Resolver`/`Pick`, `volumes.Volume`, `events.Collector`.
- Produces:
  - `type Deps struct{ Config Config; Source Source; Stager artifacts.Stager; Hasher filewatch.Hasher; Stat func(string) (int64, error); Identity identity.Resolver; ProcessInfo func(uint32) map[string]any; Volumes func() []volumes.Volume; Now func() time.Time; ReportEvery time.Duration }`
  - `New(Deps) *Collector` (реализует `events.Collector`, `Name() == "netupload"`)
  - `ProcessInfo(pid uint32) map[string]any` (Windows — путь образа; иначе `nil`)
  - `NewSource() Source` (не Windows — `nil`)
  - `collectors.Platform` получает поля `NetSource netupload.Source`, `ProcessInfo func(uint32) map[string]any`

- [ ] **Step 1: Падающие тесты сборщика**

`agent/internal/collectors/netupload/collector_test.go`:

```go
package netupload

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net/netip"
	"sync"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

type scriptSource struct {
	feed []Event
	err  error
	done chan struct{}
}

func (s *scriptSource) Run(ctx context.Context, sink func(Event)) error {
	if s.err != nil {
		return s.err
	}
	for _, ev := range s.feed {
		sink(ev)
	}
	select {
	case <-ctx.Done():
	case <-s.done:
	}
	return nil
}

type collected struct {
	mu   sync.Mutex
	list []events.Envelope
}

func (c *collected) emit(env events.Envelope) {
	c.mu.Lock()
	c.list = append(c.list, env)
	c.mu.Unlock()
}

func (c *collected) snapshot() []events.Envelope {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]events.Envelope(nil), c.list...)
}

func (c *collected) waitFor(t *testing.T, count int) []events.Envelope {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if got := c.snapshot(); len(got) >= count {
			return got
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("ждали %d событий, получили %v", count, c.snapshot())
	return nil
}

type nopCloser struct{ io.Reader }

func (nopCloser) Close() error { return nil }

func hasherWith(content []byte, openErr error) filewatch.Hasher {
	return filewatch.Hasher{
		Open: func(string) (io.ReadCloser, int64, error) {
			if openErr != nil {
				return nil, 0, openErr
			}
			return nopCloser{bytes.NewReader(content)}, int64(len(content)), nil
		},
		Sleep: func(time.Duration) {},
		Now:   time.Now,
	}
}

func testDeps(source Source, hasher filewatch.Hasher) Deps {
	cfg := DefaultConfig()
	cfg.MinFileBytes = 10
	return Deps{
		Config: cfg, Source: source, Hasher: hasher,
		Stat:        func(string) (int64, error) { return 1000, nil },
		ProcessInfo: func(pid uint32) map[string]any { return map[string]any{"pid": int(pid), "path": `C:\Chrome\chrome.exe`} },
		Volumes:     func() []volumes.Volume { return []volumes.Volume{{DriveLetter: "C:", Type: volumes.TypeFixed}} },
		Now:         time.Now,
		ReportEvery: time.Hour,
	}
}

func run(t *testing.T, deps Deps) (*collected, func()) {
	t.Helper()
	out := &collected{}
	ctx, cancel := context.WithCancel(context.Background())
	finished := make(chan struct{})
	go func() {
		New(deps).Run(ctx, out.emit)
		close(finished)
	}()
	return out, func() {
		cancel()
		select {
		case <-finished:
		case <-time.After(3 * time.Second):
			t.Fatal("Run не вернулся после отмены контекста")
		}
	}
}

func uploadFeed(path string, size uint64) []Event {
	at := time.Now()
	return []Event{
		{Kind: KindDNS, Names: []string{"drive.google.com"}, Addrs: []netip.Addr{addr("142.250.1.1")}, TTL: time.Hour, At: at},
		{Kind: KindRead, PID: 9, Path: path, At: at},
		{Kind: KindSend, PID: 9, Addr: addr("142.250.1.1"), Bytes: size, At: at},
	}
}

func TestUploadProducesOneEventWithArtifact(t *testing.T) {
	source := &scriptSource{feed: uploadFeed(`C:\Users\a\Documents\plan.pdf`, 2000), done: make(chan struct{})}
	out, stop := run(t, testDeps(source, hasherWith([]byte("содержимое документа"), nil)))
	defer stop()

	got := out.waitFor(t, 1)
	env := got[0]

	if env.Channel != events.ChannelNetwork || env.Action != "upload" {
		t.Fatalf("%s/%s", env.Channel, env.Action)
	}
	if env.Subject["service"] != "gdrive" || env.Subject["src_path"] != `C:\Users\a\Documents\plan.pdf` {
		t.Fatalf("subject = %v", env.Subject)
	}
	if env.Artifact == nil || env.Artifact.SHA256 == "" {
		t.Fatalf("ждали артефакт: %+v", env.Artifact)
	}
	if env.Process["pid"] != 9 {
		t.Fatalf("process = %v", env.Process)
	}
	if len(out.snapshot()) != 1 {
		t.Fatalf("событие должно быть одно: %v", out.snapshot())
	}
}

func TestReadsOfOtherFilesAndSmallTrafficProduceNothing(t *testing.T) {
	at := time.Now()
	source := &scriptSource{feed: []Event{
		{Kind: KindDNS, Names: []string{"drive.google.com"}, Addrs: []netip.Addr{addr("142.250.1.1")}, TTL: time.Hour, At: at},
		{Kind: KindRead, PID: 9, Path: `C:\Users\a\Pictures\photo.jpg`, At: at},                  // не тот тип
		{Kind: KindRead, PID: 9, Path: `C:\Users\a\AppData\Local\x\cache.pdf`, At: at},          // исключённый путь
		{Kind: KindRead, PID: 9, Path: `C:\Users\a\Documents\plan.pdf`, At: at},                 // подходит
		{Kind: KindSend, PID: 9, Addr: addr("142.250.1.1"), Bytes: 50, At: at},                  // мелкий обмен
		{Kind: KindSend, PID: 9, Addr: addr("8.8.8.8"), Bytes: 5_000_000, At: at},               // не сервис
	}, done: make(chan struct{})}
	out, stop := run(t, testDeps(source, hasherWith([]byte("x"), nil)))

	time.Sleep(200 * time.Millisecond)
	stop()

	if got := out.snapshot(); len(got) != 0 {
		t.Fatalf("событий быть не должно: %v", got)
	}
}

func TestVanishedFileProducesNoEvent(t *testing.T) {
	source := &scriptSource{feed: uploadFeed(`C:\Users\a\Documents\gone.pdf`, 2000), done: make(chan struct{})}
	deps := testDeps(source, hasherWith(nil, errNotExist))
	out, stop := run(t, deps)

	time.Sleep(200 * time.Millisecond)
	stop()

	if got := out.snapshot(); len(got) != 0 {
		t.Fatalf("исчезнувший файл не должен давать событие: %v", got)
	}
}

func TestLockedFileStillReportsWithoutArtifact(t *testing.T) {
	source := &scriptSource{feed: uploadFeed(`C:\Users\a\Documents\busy.pdf`, 2000), done: make(chan struct{})}
	deps := testDeps(source, hasherWith(nil, errPermission))
	out, stop := run(t, deps)
	defer stop()

	got := out.waitFor(t, 1)

	if got[0].Artifact != nil || got[0].Labels["hash"] != "unavailable" {
		t.Fatalf("артефакта нет, причина в labels: %+v %v", got[0].Artifact, got[0].Labels)
	}
}

func TestUnavailableSourceIsReportedAndDoesNotCrash(t *testing.T) {
	source := &scriptSource{err: errors.New("Access is denied"), done: make(chan struct{})}
	out, stop := run(t, testDeps(source, hasherWith(nil, nil)))
	defer stop()

	got := out.waitFor(t, 1)

	if got[0].Channel != events.ChannelAgent || got[0].Action != "netupload_unavailable" {
		t.Fatalf("%s/%s", got[0].Channel, got[0].Action)
	}
	if got[0].Subject["component"] != "netupload" {
		t.Fatalf("subject = %v", got[0].Subject)
	}
}

func TestOverflowIsCountedAndReported(t *testing.T) {
	feed := make([]Event, 0, eventQueue+500)
	for i := 0; i < eventQueue+500; i++ {
		feed = append(feed, Event{Kind: KindDNS, Names: []string{"x.example"}, Addrs: []netip.Addr{addr("1.1.1.1")}, TTL: time.Minute, At: time.Now()})
	}
	source := &scriptSource{feed: feed, done: make(chan struct{})}
	deps := testDeps(source, hasherWith(nil, nil))
	deps.ReportEvery = 50 * time.Millisecond
	// Обработчик события DNS не должен успевать за выбросом: блокируем потребителя паузой через Now.
	deps.Now = func() time.Time { time.Sleep(time.Millisecond); return time.Now() }
	out, stop := run(t, deps)
	defer stop()

	got := out.waitFor(t, 1)

	if got[0].Action != "netupload_dropped" {
		t.Fatalf("%s", got[0].Action)
	}
	if count, _ := got[0].Subject["count"].(uint64); count == 0 {
		t.Fatalf("subject = %v", got[0].Subject)
	}
}
```

В начало файла рядом с импортами добавить вспомогательные ошибки (нужны `os` и `io/fs`):

```go
var (
	errNotExist   = os.ErrNotExist
	errPermission = fs.ErrPermission
)
```

с импортами `"io/fs"` и `"os"`.

Замечание по `TestOverflowIsCountedAndReported`: тест опирается на то, что потребитель медленнее источника. Если он нестабилен (потребитель успевает), заменить его на прямую проверку: вызвать `c.sink` (экспортировать вспомогательный метод `c.enqueue(ev)`) `eventQueue+100` раз без запущенного потребителя и убедиться, что счётчик `c.dropped.Load() == 100`. Это допустимая замена, реализуется в Step 3 методом `enqueue`.

- [ ] **Step 2: Убедиться, что падает**

Run: `cd agent && go test ./internal/collectors/netupload/ -run 'Upload|Reads|Vanished|Locked|Unavailable|Overflow' -v`
Expected: FAIL (`undefined: Deps`, `New`, `eventQueue`).

- [ ] **Step 3: Реализация сборщика**

`agent/internal/collectors/netupload/collector.go`:

```go
package netupload

import (
	"context"
	"log/slog"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/barysguard/agent/internal/artifacts"
	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/identity"
	"github.com/barysguard/agent/internal/volumes"
)

const (
	eventQueue       = 4096
	jobQueue         = 64
	hashDeadline     = 5 * time.Second
	defaultReport    = time.Minute
	dnsTTL           = 5 * time.Minute
	dnsCacheSize     = 8192
	maxProcesses     = 256
	maxReadsPerPID   = 64
)

type Deps struct {
	Config      Config
	Source      Source
	Stager      artifacts.Stager
	Hasher      filewatch.Hasher
	Stat        func(path string) (int64, error)
	Identity    identity.Resolver
	ProcessInfo func(pid uint32) map[string]any
	Volumes     func() []volumes.Volume
	Now         func() time.Time
	// ReportEvery — как часто сообщать о потерянных событиях.
	ReportEvery time.Duration
}

// Collector реализует events.Collector.
type Collector struct {
	deps    Deps
	filter  *Filter
	dns     *DNSCache
	reads   *Reads
	matcher *Matcher
	dropped atomic.Uint64
}

func New(deps Deps) *Collector {
	if deps.Hasher.Open == nil {
		deps.Hasher = filewatch.NewHasher()
	}
	if deps.Stat == nil {
		deps.Stat = statSize
	}
	if deps.Now == nil {
		deps.Now = time.Now
	}
	if deps.ReportEvery <= 0 {
		deps.ReportEvery = defaultReport
	}
	dns := NewDNSCache(dnsCacheSize)
	reads := NewReads(deps.Config.Window, maxProcesses, maxReadsPerPID)
	resolver := &Resolver{Catalog: NewCatalog(deps.Config.Services), DNS: dns}
	return &Collector{
		deps: deps, filter: NewFilter(deps.Config), dns: dns, reads: reads,
		matcher: NewMatcher(deps.Config, reads, resolver),
	}
}

func statSize(path string) (int64, error) {
	info, err := os.Stat(path)
	if err != nil {
		return 0, err
	}
	if info.IsDir() {
		return 0, os.ErrInvalid
	}
	return info.Size(), nil
}

func (c *Collector) Name() string { return "netupload" }

// enqueue кладёт событие источника в очередь; переполнение учитывается.
func (c *Collector) enqueue(in chan<- Event, ev Event) {
	select {
	case in <- ev:
	default:
		c.dropped.Add(1)
	}
}

func (c *Collector) Run(ctx context.Context, emit func(events.Envelope)) error {
	var workers sync.WaitGroup
	defer workers.Wait()
	ctx, cancel := context.WithCancel(ctx)
	defer cancel() // отменяется раньше workers.Wait: defer выполняются в обратном порядке

	in := make(chan Event, eventQueue)
	jobs := make(chan Match, jobQueue)
	sourceDone := make(chan error, 1)
	go func() {
		sourceDone <- c.deps.Source.Run(ctx, func(ev Event) { c.enqueue(in, ev) })
	}()

	// Снятие копии читает диск и может ждать занятый файл: отдельный поток,
	// чтобы разбор событий не стоял.
	workers.Add(1)
	go func() {
		defer workers.Done()
		for {
			select {
			case <-ctx.Done():
				return
			case match := <-jobs:
				c.report(match, emit)
			}
		}
	}()

	ticker := time.NewTicker(c.deps.ReportEvery)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case err := <-sourceDone:
			if err != nil && ctx.Err() == nil {
				slog.Warn("сборщик netupload недоступен", "error", err)
				c.emitAgent(emit, "netupload_unavailable", events.SeverityMedium, map[string]any{
					"component": "netupload", "detail": err.Error(),
				})
			}
			return nil
		case ev := <-in:
			c.handle(ev, jobs)
		case <-ticker.C:
			if count := c.dropped.Swap(0); count > 0 {
				c.emitAgent(emit, "netupload_dropped", events.SeverityLow, map[string]any{
					"component": "netupload", "count": count,
				})
			}
		}
	}
}

func (c *Collector) emitAgent(emit func(events.Envelope), action, severity string, subject map[string]any) {
	env, err := events.NewEnvelope(events.ChannelAgent, action, severity, subject)
	if err != nil {
		slog.Warn("не удалось создать служебное событие", "action", action, "error", err)
		return
	}
	emit(env)
}

func (c *Collector) handle(ev Event, jobs chan<- Match) {
	switch ev.Kind {
	case KindDNS:
		ttl := ev.TTL
		if ttl <= 0 {
			ttl = dnsTTL
		}
		c.dns.Learn(ev.Names, ev.Addrs, ttl, ev.At)
	case KindRead:
		if !c.filter.PathOK(ev.Path) {
			return
		}
		size, err := c.deps.Stat(ev.Path)
		if err != nil || !c.filter.SizeOK(size) {
			return
		}
		c.reads.Add(ev.PID, Read{Path: ev.Path, Size: size, At: ev.At})
	case KindSend:
		for _, match := range c.matcher.Observe(Send{PID: ev.PID, Addr: ev.Addr, Bytes: ev.Bytes, At: ev.At}) {
			select {
			case jobs <- match:
			default:
				c.dropped.Add(1)
			}
		}
	}
}

// report снимает копию и отправляет событие. Исчезнувший файл события не даёт.
func (c *Collector) report(match Match, emit func(events.Envelope)) {
	deadline := c.deps.Now().Add(hashDeadline)
	hash := c.deps.Hasher.HashStaged(match.Read.Path, c.deps.Config.MaxFileBytes, deadline, c.deps.Stager)
	if hash.Status == filewatch.HashGone {
		return
	}
	var vols []volumes.Volume
	if c.deps.Volumes != nil {
		vols = c.deps.Volumes()
	}
	env, err := BuildEvent(match, hash, filewatch.VolumeFor(match.Read.Path, vols))
	if err != nil {
		slog.Warn("не удалось собрать событие отправки файла", "error", err)
		return
	}
	if c.deps.ProcessInfo != nil {
		env.Process = c.deps.ProcessInfo(match.PID)
	}
	if env.Process == nil {
		env.Labels["process"] = "unknown"
	}
	env.Actor = c.actor(match.Read.Path)
	emit(env)
}

func (c *Collector) actor(path string) map[string]any {
	if c.deps.Identity == nil {
		return nil
	}
	return identity.Pick(c.deps.Identity.FileOwner(path), c.deps.Identity.ConsoleUser())
}
```

Выполнить `gofmt -w` на пакете (выравнивание блока `const`).

`agent/internal/collectors/netupload/process_windows.go`:

```go
//go:build windows

package netupload

import "golang.org/x/sys/windows"

// ProcessInfo возвращает pid и путь образа процесса или nil, если процесс уже
// завершился или недоступен.
func ProcessInfo(pid uint32) map[string]any {
	handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return nil
	}
	defer windows.CloseHandle(handle)
	buffer := make([]uint16, windows.MAX_LONG_PATH)
	size := uint32(len(buffer))
	if err := windows.QueryFullProcessImageName(handle, 0, &buffer[0], &size); err != nil {
		return nil
	}
	return map[string]any{"pid": int(pid), "path": windows.UTF16ToString(buffer[:size])}
}
```

`agent/internal/collectors/netupload/process_other.go`:

```go
//go:build !windows

package netupload

// ProcessInfo вне Windows не определяет процесс.
func ProcessInfo(uint32) map[string]any { return nil }
```

`agent/internal/collectors/netupload/source_other.go`:

```go
//go:build !windows

package netupload

// NewSource вне Windows источника нет: сборщик не запускается.
func NewSource() Source { return nil }
```

Для компиляции на Windows до Task 6 нужна временная заглушка `source_windows.go`:

```go
//go:build windows

package netupload

import (
	"context"
	"errors"
)

// Временная заглушка: настоящий источник ETW появляется в следующей задаче.
type etwSource struct{}

func NewSource() Source { return etwSource{} }

func (etwSource) Run(context.Context, func(Event)) error {
	return errors.New("источник ETW ещё не реализован")
}
```

- [ ] **Step 4: Тесты сборщика проходят**

Run: `cd agent && gofmt -l ./internal/collectors/netupload; go test ./internal/collectors/netupload/ -v -race`
Expected: PASS (если `-race` недоступен на машине без cgo — запустить без него).

- [ ] **Step 5: Падающие тесты фабрики**

В `agent/internal/collectors/factory_test.go` добавить (в стиле соседних тестов; `plat` собирается как в `TestDefaultDocumentStartsHubUSBAndFilewatch` — посмотреть, как он создаёт `Platform`, и повторить с добавлением `NetSource`):

```go
type fakeNetSource struct{}

func (fakeNetSource) Run(context.Context, func(netupload.Event)) error { return nil }

func TestNetUploadIsAddedWhenASourceExists(t *testing.T) {
	plat := fakePlatform() // тот же помощник, что в соседних тестах
	plat.NetSource = fakeNetSource{}

	list := Build(map[string]any{}, t.TempDir(), plat)

	if !hasCollector(list, "netupload") {
		t.Fatalf("netupload должен быть в группе: %v", names(list))
	}

	off := map[string]any{"collectors": map[string]any{"net_upload": map[string]any{"enabled": false}}}
	if hasCollector(Build(off, t.TempDir(), plat), "netupload") {
		t.Fatal("выключенный net_upload не должен запускаться")
	}
}

func TestNetUploadAbsentWithoutASource(t *testing.T) {
	plat := fakePlatform() // NetSource == nil

	if hasCollector(Build(map[string]any{}, t.TempDir(), plat), "netupload") {
		t.Fatal("без источника сборщика нет (Linux, Docker-стенд)")
	}
}

func TestNetUploadAloneStartsTheGroup(t *testing.T) {
	plat := fakePlatform()
	plat.NetSource = fakeNetSource{}
	doc := map[string]any{"collectors": map[string]any{
		"usb":        map[string]any{"enabled": false},
		"file_watch": map[string]any{"enabled": false},
	}}

	list := Build(doc, t.TempDir(), plat)

	if !hasCollector(list, "netupload") {
		t.Fatalf("netupload должен работать и без usb/file_watch: %v", names(list))
	}
}
```

`fakePlatform`, `hasCollector`, `names` — помощники: если таких в файле нет, добавить в этот же файл (`fakePlatform` — копия сборки `Platform` из `TestDefaultDocumentStartsHubUSBAndFilewatch`; `hasCollector` перебирает `list` по `Name()`; `names` возвращает `[]string`). Добавить импорты `context` и `netupload`.

Run: `cd agent && go test ./internal/collectors/ -run NetUpload -v`
Expected: FAIL (`plat.NetSource undefined`).

- [ ] **Step 6: Реализация подключения**

`agent/internal/collectors/factory.go`:
- импорт `"github.com/barysguard/agent/internal/collectors/netupload"`;
- в `Platform` добавить поля:

```go
	// NetSource — источник событий чтения файлов и сети (ETW); nil — сборщика netupload нет.
	NetSource netupload.Source
	// ProcessInfo определяет процесс по PID для события отправки.
	ProcessInfo func(pid uint32) map[string]any
```

- в `DefaultPlatform` добавить `NetSource: netupload.NewSource(),` и `ProcessInfo: netupload.ProcessInfo,`;
- в `Build` заменить блок до создания `hub`:

```go
	useUSB := enabled(document, "usb")
	var profiles []string
	if plat.Profiles != nil {
		profiles = plat.Profiles()
	}
	cfg := filewatch.ConfigFromDocument(document, profiles, dataDir)
	useFiles := cfg.Enabled
	netCfg := netupload.ConfigFromDocument(document)
	useNet := plat.NetSource != nil && netCfg.Enabled
	if !useUSB && !useFiles && !useNet {
		return nil
	}
```

и после добавления `filewatch` в `list` перед `return list`:

```go
	if useNet {
		list = append(list, netupload.New(netupload.Deps{
			Config: netCfg, Source: plat.NetSource, Stager: plat.Stager,
			Identity: plat.Identity, ProcessInfo: plat.ProcessInfo, Volumes: hub.Current,
		}))
	}
```

Обновить комментарий над `Build`: «Опрос томов (hub) нужен всем сборщикам и запускается, если включён хотя бы один из них».

- [ ] **Step 7: Все тесты агента и сборка под платформы**

Run: `cd agent && go test ./... && go vet ./... && GOOS=windows go vet ./... && GOOS=linux go build ./...`
Expected: PASS и чисто.

- [ ] **Step 8: Коммит**

```bash
git add agent
git commit -m "feat(agent): netupload collector wired into the collectors factory

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Агент — источник ETW (Windows) и пробник провайдеров

**Files:**
- Modify: `agent/go.mod`, `agent/go.sum`, `agent/internal/collectors/netupload/source_windows.go` (заменить заглушку)
- Create: `agent/internal/collectors/netupload/ids_windows.go`, `dosdevices_windows.go`, `agent/cmd/etwprobe/main_windows.go`

**Interfaces:**
- Consumes (Task 4): `Source`, `Event`, `Kind*`, `ParseDNSResults`, `ntToDOS`.
- Produces: настоящий `NewSource() Source` для Windows. Внешний контракт не меняется.

Эта задача содержит единственное место с неподтверждённой информацией: числовые идентификаторы событий ETW и имена свойств. Все они собраны в `ids_windows.go` и проверяются пробником (Step 3–4). Если пробник покажет другие значения, правится **только** этот файл (и, при другом имени свойства, соответствующая строка в `source_windows.go`).

- [ ] **Step 1: Зависимость**

Run: `cd agent && go get github.com/bi-zone/etw@latest && go mod tidy`
Затем посмотреть реальный API: `go doc github.com/bi-zone/etw`, `go doc github.com/bi-zone/etw.Session`, `go doc github.com/bi-zone/etw.Event`. Ожидаемое (по документации пакета): `NewSession(guid windows.GUID, opts ...Option) (*Session, error)`, опции `WithName`, `WithMatchKeywords(any, all uint64)`, `(*Session).Process(cb EventCallback) error`, `(*Session).Close() error`, у события `Header.ID`, `Header.ProcessID`, `Header.TimeStamp`, `(*Event).EventProperties() (map[string]interface{}, error)`. Если подписи отличаются, подправить `source_windows.go` под фактический API — внешний интерфейс `Source` не меняется.

- [ ] **Step 2: Идентификаторы и пробник**

`agent/internal/collectors/netupload/ids_windows.go`:

```go
//go:build windows

package netupload

import "golang.org/x/sys/windows"

// Провайдеры ETW и номера событий. Значения подтверждаются пробником
// (agent/cmd/etwprobe): при расхождении правится только этот файл.
var (
	guidKernelFile    = mustGUID("{EDD08927-9CC4-4E65-B970-C2560FB5C289}") // Microsoft-Windows-Kernel-File
	guidKernelNetwork = mustGUID("{7DD42A49-5329-4832-8DFD-43D979153A88}") // Microsoft-Windows-Kernel-Network
	guidDNSClient     = mustGUID("{1C95126E-7EEA-49A9-A3FE-A378B03DDB4D}") // Microsoft-Windows-DNS-Client
)

const (
	// Kernel-File: ключевые слова FILENAME | READ | CREATE.
	keywordsKernelFile uint64 = 0x10 | 0x20 | 0x80

	idFileCreate uint16 = 12
	idFileRead   uint16 = 15
	idFileClose  uint16 = 14

	// Kernel-Network: отправка данных.
	idTCPv4Send uint16 = 10
	idTCPv6Send uint16 = 26
	idUDPv4Send uint16 = 42
	idUDPv6Send uint16 = 58

	// DNS-Client: завершённый запрос.
	idDNSQueryDone uint16 = 3008
)

// Имена свойств событий.
const (
	propFileObject   = "FileObject"
	propFileName     = "FileName"
	propNetPID       = "PID"
	propNetSize      = "size"
	propNetDest      = "daddr"
	propDNSName      = "QueryName"
	propDNSResults   = "QueryResults"
)

func mustGUID(text string) windows.GUID {
	guid, err := windows.GUIDFromString(text)
	if err != nil {
		panic(err)
	}
	return guid
}
```

`agent/cmd/etwprobe/main_windows.go`:

```go
//go:build windows

// etwprobe печатает первые события трёх провайдеров ETW, нужных netupload:
// номер события и имена/значения свойств. Запускать от администратора:
//
//	go run ./cmd/etwprobe
//
// Затем открыть сайт, скопировать файл, загрузить документ в облако. По выводу
// сверяются номера и имена из internal/collectors/netupload/ids_windows.go.
package main

import (
	"fmt"
	"os"
	"os/signal"
	"sync"
	"time"

	"github.com/bi-zone/etw"
	"golang.org/x/sys/windows"
)

type provider struct {
	name     string
	guid     string
	keywords uint64
}

func main() {
	providers := []provider{
		{"Kernel-File", "{EDD08927-9CC4-4E65-B970-C2560FB5C289}", 0x10 | 0x20 | 0x80},
		{"Kernel-Network", "{7DD42A49-5329-4832-8DFD-43D979153A88}", 0},
		{"DNS-Client", "{1C95126E-7EEA-49A9-A3FE-A378B03DDB4D}", 0},
	}
	var out sync.Mutex
	var sessions []*etw.Session
	for _, p := range providers {
		guid, err := windows.GUIDFromString(p.guid)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		opts := []etw.Option{etw.WithName("BarysGuard-Probe-" + p.name)}
		if p.keywords != 0 {
			opts = append(opts, etw.WithMatchKeywords(p.keywords, 0))
		}
		session, err := etw.NewSession(guid, opts...)
		if err != nil {
			fmt.Fprintf(os.Stderr, "%s: %v\n", p.name, err)
			os.Exit(1)
		}
		sessions = append(sessions, session)
		name := p.name
		go session.Process(func(e *etw.Event) {
			props, _ := e.EventProperties()
			out.Lock()
			defer out.Unlock()
			fmt.Printf("%-14s id=%-5d pid=%-6d %v\n", name, e.Header.ID, e.Header.ProcessID, props)
		})
	}
	interrupt := make(chan os.Signal, 1)
	signal.Notify(interrupt, os.Interrupt)
	select {
	case <-interrupt:
	case <-time.After(60 * time.Second):
	}
	for _, session := range sessions {
		session.Close()
	}
}
```

- [ ] **Step 3: Запустить пробник и сверить значения (вручную, от администратора)**

Run (PowerShell от администратора, из `agent/`): `go run ./cmd/etwprobe`
В течение минуты: открыть в Chrome любой сайт, скопировать любой `.pdf` в другую папку, загрузить небольшой файл на Google Drive (веб).
Проверить по выводу:
1. `Kernel-File` — есть событие с `FileName` и `FileObject` (создание) и событие с `FileObject` и размером чтения (чтение); записать их `id`.
2. `Kernel-Network` — есть отправки с `daddr`, `size`, `PID`; записать `id` для TCP IPv4/IPv6 и UDP.
3. `DNS-Client` — есть событие с `QueryName` и `QueryResults`; записать `id`.
4. Типы значений: `daddr` — `net.IP`, строка или число; `FileObject` — число.
Если номера или имена отличаются от `ids_windows.go`, поправить константы и записать расхождения в сообщение коммита. Если сессия не стартует с ошибкой «already exists» — предыдущий запуск не закрыл сессию: `logman stop "BarysGuard-Probe-Kernel-File" -ets` (аналогично для остальных).

- [ ] **Step 4: Реализация источника**

`agent/internal/collectors/netupload/dosdevices_windows.go`:

```go
//go:build windows

package netupload

import (
	"strings"
	"sync"
	"time"

	"golang.org/x/sys/windows"
)

const dosRefreshEvery = 5 * time.Second

// queryDosDevices строит карту «устройство → буква диска» для всех букв A..Z.
func queryDosDevices() map[string]string {
	devices := map[string]string{}
	buffer := make([]uint16, 1024)
	for letter := 'A'; letter <= 'Z'; letter++ {
		drive := string(letter) + ":"
		name, err := windows.UTF16PtrFromString(drive)
		if err != nil {
			continue
		}
		n, err := windows.QueryDosDevice(name, &buffer[0], uint32(len(buffer)))
		if err != nil || n == 0 {
			continue
		}
		target := windows.UTF16ToString(buffer[:n])
		devices[strings.ToLower(target)] = drive
	}
	return devices
}

// dosMap переводит NT-пути в DOS. Новый том (флешка) появляется между
// запросами, поэтому при промахе карта обновляется, но не чаще раза в 5 секунд.
type dosMap struct {
	mu        sync.Mutex
	devices   map[string]string
	refreshed time.Time
}

func newDosMap() *dosMap {
	return &dosMap{devices: queryDosDevices(), refreshed: time.Now()}
}

func (d *dosMap) toDOS(path string) string {
	d.mu.Lock()
	defer d.mu.Unlock()
	if dos, ok := ntToDOS(path, d.devices); ok {
		return dos
	}
	if time.Since(d.refreshed) > dosRefreshEvery {
		d.devices = queryDosDevices()
		d.refreshed = time.Now()
		if dos, ok := ntToDOS(path, d.devices); ok {
			return dos
		}
	}
	return ""
}
```

`agent/internal/collectors/netupload/source_windows.go` (заменить заглушку целиком):

```go
//go:build windows

package netupload

import (
	"context"
	"fmt"
	"net"
	"net/netip"
	"strings"
	"sync"
	"time"

	"github.com/bi-zone/etw"
	"golang.org/x/sys/windows"
)

const maxFileObjects = 200_000

type fileEntry struct {
	path     string
	reported bool
}

// fileTable связывает объект файла из событий Kernel-File с путём (путь есть
// только в событии создания). Размер ограничен: при переполнении таблица
// сбрасывается, часть чтений будет пропущена, но память не растёт.
type fileTable struct {
	mu      sync.Mutex
	entries map[uint64]*fileEntry
}

func newFileTable() *fileTable { return &fileTable{entries: map[uint64]*fileEntry{}} }

func (t *fileTable) set(object uint64, path string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if len(t.entries) >= maxFileObjects {
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

type etwSource struct{}

func NewSource() Source { return etwSource{} }

type feed struct {
	name     string
	guid     windows.GUID
	keywords uint64
	handle   func(*etw.Event)
}

func (etwSource) Run(ctx context.Context, sink func(Event)) error {
	files := newFileTable()
	dos := newDosMap()
	feeds := []feed{
		{"BarysGuard-NetUpload-File", guidKernelFile, keywordsKernelFile, func(e *etw.Event) { handleFile(e, files, dos, sink) }},
		{"BarysGuard-NetUpload-Net", guidKernelNetwork, 0, func(e *etw.Event) { handleNetwork(e, sink) }},
		{"BarysGuard-NetUpload-DNS", guidDNSClient, 0, func(e *etw.Event) { handleDNS(e, sink) }},
	}

	var sessions []*etw.Session
	closeAll := func() {
		for _, session := range sessions {
			_ = session.Close()
		}
	}
	for _, f := range feeds {
		options := []etw.Option{etw.WithName(f.name)}
		if f.keywords != 0 {
			options = append(options, etw.WithMatchKeywords(f.keywords, 0))
		}
		session, err := etw.NewSession(f.guid, options...)
		if err != nil {
			closeAll()
			return fmt.Errorf("сессия ETW %s: %w", f.name, err)
		}
		sessions = append(sessions, session)
	}

	results := make(chan error, len(sessions))
	for index, session := range sessions {
		handle := feeds[index].handle
		go func() { results <- session.Process(handle) }()
	}

	select {
	case <-ctx.Done():
		closeAll()
		for range sessions {
			<-results
		}
		return nil
	case err := <-results:
		closeAll()
		for i := 1; i < len(sessions); i++ {
			<-results
		}
		if err == nil {
			err = fmt.Errorf("сессия ETW завершилась без ошибки")
		}
		return err
	}
}

func handleFile(e *etw.Event, files *fileTable, dos *dosMap, sink func(Event)) {
	switch e.Header.ID {
	case idFileCreate:
		props, err := e.EventProperties()
		if err != nil {
			return
		}
		object, ok := asUint(props[propFileObject])
		name, _ := props[propFileName].(string)
		if !ok || name == "" {
			return
		}
		if path := dos.toDOS(name); path != "" {
			files.set(object, path)
		}
	case idFileRead:
		props, err := e.EventProperties()
		if err != nil {
			return
		}
		object, ok := asUint(props[propFileObject])
		if !ok {
			return
		}
		if path, first := files.firstRead(object); first {
			sink(Event{Kind: KindRead, PID: e.Header.ProcessID, Path: path, At: e.Header.TimeStamp})
		}
	case idFileClose:
		props, err := e.EventProperties()
		if err != nil {
			return
		}
		if object, ok := asUint(props[propFileObject]); ok {
			files.drop(object)
		}
	}
}

func handleNetwork(e *etw.Event, sink func(Event)) {
	switch e.Header.ID {
	case idTCPv4Send, idTCPv6Send, idUDPv4Send, idUDPv6Send:
	default:
		return
	}
	props, err := e.EventProperties()
	if err != nil {
		return
	}
	size, ok := asUint(props[propNetSize])
	dest, destOK := asAddr(props[propNetDest])
	if !ok || !destOK || size == 0 {
		return
	}
	pid := e.Header.ProcessID
	if owner, ok := asUint(props[propNetPID]); ok && owner != 0 {
		pid = uint32(owner)
	}
	sink(Event{Kind: KindSend, PID: pid, Addr: dest, Bytes: size, At: e.Header.TimeStamp})
}

func handleDNS(e *etw.Event, sink func(Event)) {
	if e.Header.ID != idDNSQueryDone {
		return
	}
	props, err := e.EventProperties()
	if err != nil {
		return
	}
	name, _ := props[propDNSName].(string)
	results, _ := props[propDNSResults].(string)
	names, addrs := ParseDNSResults(name, results)
	if len(names) == 0 || len(addrs) == 0 {
		return
	}
	sink(Event{Kind: KindDNS, Names: names, Addrs: addrs, TTL: 5 * time.Minute, At: e.Header.TimeStamp})
}

// asUint приводит числовое свойство события к uint64.
func asUint(value any) (uint64, bool) {
	switch v := value.(type) {
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

// asAddr приводит адрес назначения к netip.Addr: свойство бывает net.IP,
// строкой или байтами.
func asAddr(value any) (netip.Addr, bool) {
	switch v := value.(type) {
	case net.IP:
		addr, ok := netip.AddrFromSlice(v)
		return addr.Unmap(), ok
	case []byte:
		addr, ok := netip.AddrFromSlice(v)
		return addr.Unmap(), ok
	case string:
		addr, err := netip.ParseAddr(strings.TrimSpace(v))
		return addr.Unmap(), err == nil
	}
	return netip.Addr{}, false
}
```

Если Step 3 показал, что `daddr` приходит числом (`uint32`, порядок байт сети), добавить в `asAddr` ветку `uint32` → `netip.AddrFrom4([4]byte{byte(v), byte(v >> 8), byte(v >> 16), byte(v >> 24)})` и проверить порядок на реальном трафике.

- [ ] **Step 5: Сборка и юнит-тесты**

Run: `cd agent && GOOS=windows go vet ./... && go build ./... && GOOS=linux go build ./... && go test ./...`
Expected: чисто и PASS. (Для `asUint`/`asAddr`/`fileTable` добавить небольшой тест `source_windows_test.go` с тегом `windows`: `asUint(uint32(5))`, `asUint(int32(-1))` → false, `asAddr(net.ParseIP("1.2.3.4"))`, `asAddr("::ffff:1.2.3.4")` → `1.2.3.4`; `fileTable.firstRead` возвращает путь один раз; после `maxFileObjects` вставок размер не превышает предел.)

- [ ] **Step 6: Ручная проверка источника (Windows, от администратора)**

1. Собрать агент: `cd agent; go build -o C:\BarysGuardTest\barysguard-agent.exe .\cmd\barysguard-agent`.
2. Остановить прежний экземпляр агента, если запущен (`Get-Process barysguard-agent`).
3. Запустить: `& C:\BarysGuardTest\barysguard-agent.exe run -data-dir C:\BarysGuardTest\data` (PowerShell от администратора).
4. В Chrome открыть `https://drive.google.com` и загрузить файл `plan.pdf` (≥ 1 КБ) из «Документов».
5. Через 30–60 секунд в консоли «События» фильтр «Сеть»: событие «Отправка файла», путь и сервис «Google Drive».
Если события нет: смотреть лог агента на `netupload`, затем вывод `etwprobe` (расхождения идентификаторов).

- [ ] **Step 7: Коммит**

```bash
git add agent
git commit -m "feat(agent): ETW source for file reads, network sends and DNS

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Документация, спека, сквозная проверка

**Files:**
- Create: `docs/NETWORK_UPLOAD.md`
- Modify: `docs/superpowers/specs/2026-10-09-network-upload-n1-design.md`, `docs/COLLECTORS_MANUAL.md` (одна строка-ссылка)

- [ ] **Step 1: Написать `docs/NETWORK_UPLOAD.md`**

Разделы (на русском, в стиле `docs/COLLECTORS_MANUAL.md` и `docs/DLP_WORKER.md`):
1. **Что делает** — одним абзацем: ловит отправку документа в облако или мессенджер по связке «прочитал → отправил», снимает копию, воркер проверяет правилами.
2. **Как работает** — схема потока из спеки (раздел 3), три источника ETW, окно, допуск, формула сопоставления из Global Constraints.
3. **Что видно оператору** — событие «Сеть → Отправка файла», поля `subject`, уверенность `high/medium`, инцидент «Отправка файла в сеть: …».
4. **Настройка** — таблица `collectors.net_upload` (ключи и умолчания из спеки, раздел 5, с ключом `net_upload`), правило «пустой список = встроенное значение», пример добавления сервиса.
5. **Требования и ограничения** — Windows, запуск от администратора; DoH без DNS (запасной путь по диапазонам); файл изменён перед отправкой; текст сообщений и форм не читается (подпроект N2); потерянные события ETW библиотека не показывает, видны только переполнения очереди (`agent/netupload_dropped`); каталог сервисов стартовый и неполный.
6. **Ручная проверка** — шаги из Task 6, Step 6 плюс проверка Telegram Desktop (отправить документ из «Документов» в «Избранное» → событие с сервисом «Telegram», определённым по диапазону адресов) и отрицательные проверки (картинка `.jpg` и мелкий документ не дают событий; файл из `AppData` не даёт).
7. **Если событий нет** — чек-лист: агент от администратора; в логе нет `netupload недоступен`; `etwprobe`; домен сервиса есть в каталоге; расширение в списке; размер файла в пределах.

- [ ] **Step 2: Поправить спеку**

В `docs/superpowers/specs/2026-10-09-network-upload-n1-design.md`:
- строку «**Статус:** проект, ждёт одобрения» заменить на «**Статус:** реализовано (подпроект N1)»;
- в разделе 5 заменить `collectors.netupload` на `collectors.net_upload` и добавить строку «Пустой список в `extensions`, `exclude_paths`, `services` означает встроенное значение агента»;
- в таблицу полей события (раздел 4) добавить строку `service_name | название сервиса для консоли`;
- в раздел 6 после пункта про потерю событий ETW дописать: «Библиотека ETW не отдаёт счётчик потерянных событий; сообщается только о переполнении собственной очереди сборщика (`agent/netupload_dropped`).»;
- раздел 11 («Открытые вопросы») заменить итогом: «Библиотека ETW — `github.com/bi-zone/etw`; идентификаторы событий и имена свойств подтверждены пробником `cmd/etwprobe` (см. `ids_windows.go`); встроенный каталог — стартовый, расширяется конфигурацией.»

- [ ] **Step 3: Ссылка из руководства сборщиков**

В `docs/COLLECTORS_MANUAL.md` в конец раздела о загрузке содержимого добавить одну строку: «Отправка файлов в облака и мессенджеры — см. `docs/NETWORK_UPLOAD.md`.»

- [ ] **Step 4: Полная проверка**

Run: из `server/` — полный pytest, `ruff check .`, `ruff format --check .`, `mypy barysguard`; из `agent/` — `go test ./...`, `go vet ./...`, `GOOS=windows go vet ./...`, `GOOS=linux go build ./...`; из `web/` — `npx vitest run`, `npm run typecheck`, `npm run build`.
Expected: всё зелёное.

- [ ] **Step 5: Стенд**

`.\stand.cmd up`, `.\smoke.cmd` (все шаги ok — Linux-агенты стенда сборщик netupload не запускают), `.\stand.cmd seed`. Проверить, что агент стенда получает конфигурацию с разделом `net_upload` без ошибок (`.\stand.cmd logs agent-1` — нет ошибок разбора конфигурации).

- [ ] **Step 6: Коммит**

```bash
git add docs
git commit -m "docs: network upload detection guide and spec status

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** раздел 3 (компоненты `etwsession`/`reads`/`dnsmap`/`matcher`) → Tasks 3 и 6; раздел 4 (событие) → Tasks 1 (сервер) и 4 (`BuildEvent`); раздел 5 (конфигурация) → Tasks 1 (модель сервера) и 2 (разбор агента); раздел 6 (нагрузка/надёжность): ограничение памяти — Tasks 3 (Reads, DNSCache) и 6 (`fileTable`); дубли — Task 3 (`reported`); недоступность ETW — Tasks 5–6; исчезнувший/занятый файл — Task 5; DoH — запасной путь по CIDR (Tasks 2–3); раздел 7 (сервер) → Task 1; раздел 8 (приватность) — `BuildEvent` не содержит содержимого; раздел 9 (тестирование) — тесты в каждой задаче; раздел 10 (порядок) соблюдён (сервер → ядро → ETW → документация).

**Отклонения от спеки, принятые в плане:** (1) ключ раздела `net_upload` вместо `netupload`; (2) в событие добавлено `service_name`; (3) библиотека ETW не отдаёт счётчик потерянных событий — сообщается только переполнение очереди; (4) пустой список в конфигурации = встроенное значение. Все четыре фиксируются в спеке (Task 7, Step 2).

**Placeholder scan:** шаги содержат полный код; неподтверждённым остаётся только содержимое `ids_windows.go` (номера событий и имена свойств ETW) и тип значения `daddr` — это явно вынесено в Task 6 с пробником и инструкцией, что править при расхождении. Помощники `fakePlatform`/`hasCollector`/`names` в тестах фабрики указаны как «взять из соседних тестов или добавить»; их форма однозначна.

**Type consistency:** `Config`/`Service` (Task 2) → `Catalog`, `Filter`, `Matcher`, `Collector`; `Match` (Task 3) → `BuildEvent` (Task 4) и `report` (Task 5); `Event`/`Source` (Task 4) → `Collector.handle` (Task 5) и `etwSource` (Task 6); `Read.Size` — `int64`, `Match.Sent` — `uint64`, в событии `size_bytes` — `int64`, `sent_bytes` — `uint64`; `ConfidenceHigh/Medium` совпадают с серверным описанием `high|medium`.
