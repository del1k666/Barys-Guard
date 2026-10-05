# Сборщики «Файлы и USB» (подпроект 2b-1) — план реализации

> **Для исполнителя:** ОБЯЗАТЕЛЬНЫЙ поднавык — `superpowers:subagent-driven-development` (рекомендуется) или `superpowers:executing-plans`. Шаги отмечаются чекбоксами (`- [ ]`).

**Цель:** агент под Windows присылает события подключения внешних носителей и операций с файлами (с хешем, пользователем, процессом и признаком копирования) в уже готовый конвейер событий.

**Архитектура:** один опрос томов (`volumes.Hub`) кормит два сборщика: `usb` (события `usb/mount|unmount`) и `filewatch` (наблюдатели `ReadDirectoryChangesW` по наблюдаемым папкам и корням внешних томов → склейка → хеширование → индекс хешей для `copy` → пользователь и процесс). Группа сборщиков строится фабрикой из документа конфигурации и перезапускается, когда меняется раздел `collectors`. Windows-вызовы лежат в `*_windows.go`, остальные платформы получают заглушки; логика переносимая и тестируется без Windows.

**Стек:** Go 1.23 (`golang.org/x/sys/windows`, `LazySystemDLL` для WTS и Restart Manager), Python 3.12 / pydantic для схемы конфигурации и проверки `subject` на сервере.

**Спека:** `docs/superpowers/specs/2026-10-05-collectors-files-usb-design.md` (конвейер событий — `2026-10-05-event-pipeline-design.md`).

## Глобальные ограничения

- Агент — модуль `agent/` (`go 1.23`); новых зависимостей нет (только `golang.org/x/sys`, уже подключён). Сервер — без новых зависимостей.
- Windows-специфичный код — только в файлах `*_windows.go`; к каждому — парный `*_other.go` с заглушкой (`//go:build !windows`), чтобы агент собирался в Docker-стенде (Linux). Переносимая логика не импортирует `golang.org/x/sys/windows`.
- Комментарии и сообщения журнала — на русском, объясняют **почему**.
- Каналы и действия: `file` — `create | modify | rename | delete | copy`; `usb` — `mount | unmount`; `volume.type` — `fixed | removable | network | unknown`.
- Значения по умолчанию (агент и сервер совпадают): `usb.enabled=true`, `usb.poll_seconds=2`; `file_watch.enabled=true`, `paths=[%USERS%\Documents, %USERS%\Desktop, %USERS%\Downloads]`, `exclude=[*\~$*, *.tmp, *.crdownload, *\AppData\*]`, `stable_ms=1500`, `max_wait_ms=30000`, `max_hash_bytes=268435456`, `max_events_per_second=200`.
- Границы значений на сервере: `poll_seconds` 1–60; `stable_ms` 200–60000; `max_wait_ms` 1000–300000; `max_hash_bytes` 1 МиБ – 4 ГиБ; `max_events_per_second` 1–10000.
- Ранги критичности: `copy` → `high`; `create|modify` на внешнем томе → `medium`; `usb/mount` → `low`; остальное `info`.
- Каждый коммит завершается строкой `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (вторым `-m`).
- Серверные тесты: `export BG_TEST_DATABASE_URL=postgresql+asyncpg://barysguard:barysguard@localhost:5432/postgres` (Docker не обязателен). Команды — из Git Bash в корне репозитория.

## Review Focus

Входы, о которых спека молчит, но которые встретятся. Тест на каждый добавлен в задачу-владельца:

1. **Файл занят или огромен:** открыть нельзя — событие без хеша с `labels.hash = unavailable`, и сборщик не виснет; больше `max_hash_bytes` — `skipped_size`, а не чтение гигабайтов (задача 5).
2. **Массовая операция (распаковка тысяч файлов):** ограничитель частоты отсекает лишнее, потеря сообщается `agent/events_dropped`, сборщик не падает и не копит память (задача 5).
3. **Извлечение флешки во время записи и смена носителя в той же букве:** unmount и mount вместо молчания; наблюдатель корня останавливается без паники и утечки дескриптора (задачи 2, 6).
4. **Кириллица, пробелы, пути длиннее 260 символов, каталоги вместо файлов:** уведомления разбираются верно; каталоги не порождают событий (задачи 5, 6).
5. **Нет прав на папку другого профиля:** `agent/watch_denied` по этому корню, остальные корни работают (задача 6).
6. **Смена конфигурации без изменения раздела `collectors`:** сборщики не перезапускаются, `usb/mount` не дублируется (задача 7).

## Карта файлов

Сервер:

| Файл | Ответственность |
|---|---|
| `server/barysguard/services/config.py` | `CollectorsConfig` и вложенные модели, поле `collectors` |
| `server/barysguard/services/event_subjects.py` (новый) | `subject_is_valid` для каналов `file` и `usb` |
| `server/barysguard/services/events.py` | вызов проверки `subject` |
| `api/gateway-v1.yaml`, `web/src/api/schema.d.ts` | регенерация |

Агент:

| Файл | Ответственность |
|---|---|
| `agent/internal/volumes/volumes.go` (новый) | `Volume`, `Provider`, `ClassifyType`, `diff`, `Hub` |
| `agent/internal/volumes/descriptor.go` (новый) | разбор `STORAGE_DEVICE_DESCRIPTOR` |
| `agent/internal/volumes/provider_windows.go`, `provider_other.go` | перечень томов |
| `agent/internal/collectors/usb/usb.go` (новый) | сборщик `usb` |
| `agent/internal/identity/identity.go`, `identity_windows.go`, `identity_other.go` | владелец файла и пользователь консоли |
| `agent/internal/collectors/filewatch/` | `config.go`, `exclude.go`, `debounce.go`, `hashindex.go`, `limiter.go`, `hasher.go`, `volumeof.go`, `event.go`, `pipeline.go`, `notify.go`, `collector.go`, `watcher_windows.go`, `watcher_other.go`, `attrib_windows.go`, `attrib_other.go` |
| `agent/internal/collectors/factory.go` (новый) | `Build`, `NewFactory`, `DefaultPlatform` |
| `agent/internal/runner/events.go`, `agent.go` | фабрика, перезапуск группы |
| `agent/cmd/barysguard-agent/main.go` | подключение фабрики |

Документация: `docs/COLLECTORS_MANUAL.md` (новый), `docs/QUICKSTART.md`, спека (статус и уточнение).

---

### Task 1: Сервер — схема `collectors` и проверка `subject`

**Files:**
- Modify: `server/barysguard/services/config.py`, `server/barysguard/services/events.py`
- Create: `server/barysguard/services/event_subjects.py`
- Regenerate: `api/gateway-v1.yaml`, `web/src/api/schema.d.ts`
- Test: `server/tests/test_collectors_config.py`, `server/tests/test_event_subjects.py`; дополнить `server/tests/test_events_ingest.py`

**Interfaces:**
- Produces: `CollectorsConfig{usb: UsbCollectorConfig, file_watch: FileWatchConfig}` как поле `AgentConfigDocument.collectors`; `subject_is_valid(channel: Channel, action: str, subject: dict[str, Any]) -> bool`.

- [ ] **Step 1: Падающие тесты конфигурации `server/tests/test_collectors_config.py`**

```python
"""Раздел collectors документа конфигурации агента."""

import pytest
from pydantic import ValidationError

from barysguard.services.config import AgentConfigDocument
from tests.helpers import enroll_agent


def test_defaults_match_the_spec() -> None:
    document = AgentConfigDocument().model_dump(mode="json")["collectors"]

    assert document["usb"] == {"enabled": True, "poll_seconds": 2}
    watch = document["file_watch"]
    assert watch["enabled"] is True
    assert watch["paths"] == [
        "%USERS%\\Documents",
        "%USERS%\\Desktop",
        "%USERS%\\Downloads",
    ]
    assert watch["exclude"] == ["*\\~$*", "*.tmp", "*.crdownload", "*\\AppData\\*"]
    assert (watch["stable_ms"], watch["max_wait_ms"]) == (1500, 30000)
    assert watch["max_hash_bytes"] == 256 * 1024 * 1024
    assert watch["max_events_per_second"] == 200


@pytest.mark.parametrize(
    "patch",
    [
        {"usb": {"poll_seconds": 0}},
        {"usb": {"poll_seconds": 61}},
        {"file_watch": {"stable_ms": 100}},
        {"file_watch": {"max_wait_ms": 500}},
        {"file_watch": {"max_hash_bytes": 1024}},
        {"file_watch": {"max_events_per_second": 0}},
        {"file_watch": {"paths": [""]}},
        {"file_watch": {"typo_field": 1}},
        {"unknown_collector": {}},
    ],
)
def test_out_of_range_or_unknown_values_are_rejected(patch: dict) -> None:
    with pytest.raises(ValidationError):
        AgentConfigDocument.model_validate({"collectors": patch})


async def test_agent_receives_collectors_defaults(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "collectors-defaults")

    response = await app_client.get("/gateway/v1/config", headers=agent.headers)

    assert response.status_code == 200
    document = response.json()["document"]
    assert document["collectors"]["file_watch"]["stable_ms"] == 1500
    assert document["collectors"]["usb"]["enabled"] is True
```

- [ ] **Step 2: Падающие тесты `subject` `server/tests/test_event_subjects.py`**

```python
"""Проверка subject для каналов file и usb."""

import json
import uuid

import pytest

from barysguard.gateway.event_schemas import Channel
from barysguard.services.event_subjects import subject_is_valid
from barysguard.services.events import parse_batch

VOLUME = {"type": "removable", "serial": "0781-5583", "label": "KINGSTON", "fs": "NTFS"}


def _file(action: str, **subject) -> dict:
    return {"dst_path": "E:\\report.xlsx", "volume": VOLUME, **subject}


@pytest.mark.parametrize(
    ("action", "subject"),
    [
        ("create", _file("create")),
        ("modify", _file("modify", size_bytes=10)),
        ("delete", _file("delete")),
        ("rename", _file("rename", old_path="E:\\old.xlsx")),
        ("copy", _file("copy", src_path="C:\\Users\\u\\Documents\\report.xlsx")),
    ],
)
def test_valid_file_subjects(action: str, subject: dict) -> None:
    assert subject_is_valid(Channel.FILE, action, subject) is True


@pytest.mark.parametrize(
    ("action", "subject"),
    [
        ("teleport", _file("create")),
        ("create", {"volume": VOLUME}),
        ("create", {"dst_path": "", "volume": VOLUME}),
        ("create", {"dst_path": "E:\\x"}),
        ("create", {"dst_path": "E:\\x", "volume": {"type": "cloud"}}),
        ("rename", _file("rename")),
        ("copy", _file("copy")),
        ("create", _file("create", size_bytes=-1)),
        ("create", _file("create", size_bytes=True)),
    ],
)
def test_invalid_file_subjects(action: str, subject: dict) -> None:
    assert subject_is_valid(Channel.FILE, action, subject) is False


def test_usb_subjects() -> None:
    good = {"drive_letter": "E:", "volume": VOLUME, "device": {"bus": "usb"}}

    assert subject_is_valid(Channel.USB, "mount", good) is True
    assert subject_is_valid(Channel.USB, "unmount", good) is True
    assert subject_is_valid(Channel.USB, "eject", good) is False
    assert subject_is_valid(Channel.USB, "mount", {**good, "drive_letter": ""}) is False
    assert subject_is_valid(Channel.USB, "mount", {**good, "device": "usb"}) is False
    assert subject_is_valid(Channel.USB, "mount", {k: v for k, v in good.items() if k != "volume"}) is False


def test_other_channels_are_not_checked() -> None:
    assert subject_is_valid(Channel.AGENT, "anything", {}) is True
    assert subject_is_valid(Channel.PROCESS, "anything", {}) is True


def test_invalid_subject_rejects_only_that_event() -> None:
    def event(**overrides) -> str:
        base = {
            "event_id": str(uuid.uuid4()),
            "schema_version": 1,
            "occurred_at": "2026-10-05T10:00:00+00:00",
            "channel": "file",
            "action": "create",
            "subject": {"dst_path": "E:\\x", "volume": VOLUME},
        }
        base.update(overrides)
        return json.dumps(base)

    parsed = parse_batch("\n".join([event(subject={"volume": VOLUME}), event()]).encode(), 10)

    assert [(r.line, r.reason) for r in parsed.rejected] == [(1, "invalid_event")]
    assert [line for line, _ in parsed.events] == [2]
```

Дополнить `server/tests/test_events_ingest.py` в конец:

```python
async def test_file_and_usb_events_are_validated_end_to_end(app_client, session) -> None:
    agent = await enroll_agent(app_client, session, "ingest-channels")
    volume = {"type": "removable", "serial": "0781-5583", "label": "K", "fs": "NTFS"}

    response = await _post(
        app_client,
        agent,
        _event(
            channel="file",
            action="copy",
            subject={"dst_path": "E:\\a.docx", "src_path": "C:\\a.docx", "volume": volume},
            artifact={"sha256": "a" * 64, "size": 5, "uploaded": False},
        ),
        _event(
            channel="usb",
            action="mount",
            subject={"drive_letter": "E:", "volume": volume, "device": {"bus": "usb"}},
        ),
        _event(channel="file", action="create", subject={"dst_path": "E:\\b"}),
    )

    assert response.status_code == 202, response.text
    assert response.json()["accepted"] == 2
    assert [(r["line"], r["reason"]) for r in response.json()["rejected"]] == [(3, "invalid_event")]
```

- [ ] **Step 3: Запустить, убедиться, что падает**

Run: `cd server && .venv/Scripts/python -m pytest tests/test_collectors_config.py tests/test_event_subjects.py tests/test_events_ingest.py -q 2>&1 | tail -8`
Expected: FAIL (`collectors` нет в документе; нет модуля `event_subjects`).

- [ ] **Step 4: Модели в `server/barysguard/services/config.py`**

Перед `class AgentConfigDocument` добавить:

```python
class UsbCollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    poll_seconds: int = Field(default=2, ge=1, le=60)


def _default_watch_paths() -> list[str]:
    return ["%USERS%\\Documents", "%USERS%\\Desktop", "%USERS%\\Downloads"]


def _default_watch_exclude() -> list[str]:
    return ["*\\~$*", "*.tmp", "*.crdownload", "*\\AppData\\*"]


class FileWatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    # %USERS% агент раскрывает в каталог каждого профиля пользователя.
    paths: list[str] = Field(default_factory=_default_watch_paths, max_length=64)
    exclude: list[str] = Field(default_factory=_default_watch_exclude, max_length=64)
    stable_ms: int = Field(default=1500, ge=200, le=60_000)
    max_wait_ms: int = Field(default=30_000, ge=1_000, le=300_000)
    max_hash_bytes: int = Field(
        default=256 * 1024 * 1024, ge=1024 * 1024, le=4 * 1024 * 1024 * 1024
    )
    max_events_per_second: int = Field(default=200, ge=1, le=10_000)

    @field_validator("paths", "exclude")
    @classmethod
    def _non_empty_short_strings(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value or len(value) > 512:
                raise ValueError("each entry must be 1..512 characters")
        return values


class CollectorsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usb: UsbCollectorConfig = UsbCollectorConfig()
    file_watch: FileWatchConfig = FileWatchConfig()
```

В импорте `from pydantic import BaseModel, ConfigDict, Field` добавить `field_validator`. В `AgentConfigDocument` после `logging` добавить `collectors: CollectorsConfig = CollectorsConfig()`.

- [ ] **Step 5: `server/barysguard/services/event_subjects.py`**

```python
"""Проверка subject событий каналов file и usb.

Схема subject определяется каналом (раздел 9 основной спеки). Проверяется
только то, без чего событие бесполезно оператору или опасно для разбора;
лишние поля допускаются, чтобы агент новой версии не отбрасывался.
"""

from typing import Any

from barysguard.gateway.event_schemas import Channel

FILE_ACTIONS = frozenset({"create", "modify", "rename", "delete", "copy"})
USB_ACTIONS = frozenset({"mount", "unmount"})
VOLUME_TYPES = frozenset({"fixed", "removable", "network", "unknown"})


def _text(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _volume_ok(volume: Any) -> bool:
    return isinstance(volume, dict) and volume.get("type") in VOLUME_TYPES


def _file_ok(action: str, subject: dict[str, Any]) -> bool:
    if action not in FILE_ACTIONS:
        return False
    if not _text(subject.get("dst_path")) or not _volume_ok(subject.get("volume")):
        return False
    if action == "copy" and not _text(subject.get("src_path")):
        return False
    if action == "rename" and not _text(subject.get("old_path")):
        return False
    size = subject.get("size_bytes")
    # bool в Python — подкласс int: True не должно сойти за размер.
    return size is None or (isinstance(size, int) and not isinstance(size, bool) and size >= 0)


def _usb_ok(action: str, subject: dict[str, Any]) -> bool:
    return (
        action in USB_ACTIONS
        and _text(subject.get("drive_letter"))
        and _volume_ok(subject.get("volume"))
        and isinstance(subject.get("device"), dict)
    )


def subject_is_valid(channel: Channel, action: str, subject: dict[str, Any]) -> bool:
    if channel is Channel.FILE:
        return _file_ok(action, subject)
    if channel is Channel.USB:
        return _usb_ok(action, subject)
    return True
```

В `server/barysguard/services/events.py`: импорт `from barysguard.services.event_subjects import subject_is_valid` и в `_parse_line` после блока `try/except` с `astimezone` (перед `return envelope, None`):

```python
    if not subject_is_valid(envelope.channel, envelope.action, envelope.subject):
        return None, "invalid_event"
```

- [ ] **Step 6: Запустить тесты, форматирование, mypy**

Run: `cd server && .venv/Scripts/python -m ruff format barysguard tests >/dev/null; .venv/Scripts/python -m ruff check barysguard tests && .venv/Scripts/python -m mypy barysguard | tail -2 && .venv/Scripts/python -m pytest tests/test_collectors_config.py tests/test_event_subjects.py tests/test_events_ingest.py tests/test_agent_config.py -q 2>&1 | tail -6`
Expected: PASS. Падение `test_agent_config` на точном сравнении документа — следствие нового раздела: обновите ожидание, а не код.

- [ ] **Step 7: Регенерировать контракт и типы консоли**

```bash
cd server && .venv/Scripts/python -c "import yaml; from barysguard.main import create_app; open('../api/gateway-v1.yaml','w',encoding='utf-8',newline='\n').write(yaml.safe_dump(create_app().openapi(), sort_keys=False, allow_unicode=True))"
cd ../web && npm run types 2>&1 | tail -2 && npm run typecheck 2>&1 | tail -3 && npm test 2>&1 | tail -4
cd ../server && .venv/Scripts/python -m pytest tests/test_openapi_contract.py -q 2>&1 | tail -2
```

Expected: типы консоли пересобраны, `typecheck` и тесты web зелёные, контракт совпадает.

- [ ] **Step 8: Полный прогон сервера и коммит**

```bash
cd server && .venv/Scripts/python -m pytest -q 2>&1 | tail -3
cd .. && git add server api web/src/api/schema.d.ts
git commit -m "feat(server): collectors config section and file/usb subject validation" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Пакет `volumes` — модель, хаб, разбор дескриптора

**Files:**
- Create: `agent/internal/volumes/volumes.go`, `descriptor.go`
- Test: `agent/internal/volumes/volumes_test.go`, `descriptor_test.go`

**Interfaces:**
- Produces: `type Volume struct{DriveLetter, Serial, Label, FS string; SizeBytes int64; Type, Bus, Vendor, Product, DeviceSerial string}`; константы `TypeFixed|TypeRemovable|TypeNetwork|TypeUnknown`, `BusUSB|BusOther|BusUnknown`; `(Volume).Key() string`; `type Provider interface{Snapshot() ([]Volume, error)}`; `type Change struct{Mounted bool; Volume Volume}`; `ClassifyType(driveType, bus string) string`; `NewHub(p Provider, interval time.Duration) *Hub` с методами `Name() string`, `Run(ctx, emit) error` (реализует `events.Collector`), `Poll() error`, `Subscribe() (<-chan Change, func())`, `Current() []Volume`; `ParseStorageDescriptor(out []byte) (bus, vendor, product, serial string)`.

- [ ] **Step 1: Падающие тесты хаба `agent/internal/volumes/volumes_test.go`**

```go
package volumes

import (
	"errors"
	"testing"
)

type fakeProvider struct {
	vols []Volume
	err  error
}

func (f *fakeProvider) Snapshot() ([]Volume, error) { return f.vols, f.err }

func usb(letter, serial string) Volume {
	return Volume{DriveLetter: letter, Serial: serial, Type: TypeRemovable, Bus: BusUSB, Label: "K"}
}

func drain(ch <-chan Change) []Change {
	var out []Change
	for {
		select {
		case c := <-ch:
			out = append(out, c)
		default:
			return out
		}
	}
}

func TestFirstPollReportsEveryMountedVolume(t *testing.T) {
	provider := &fakeProvider{vols: []Volume{usb("E:", "A"), {DriveLetter: "C:", Serial: "S", Type: TypeFixed}}}
	hub := NewHub(provider, 0)
	changes, cancel := hub.Subscribe()
	defer cancel()

	if err := hub.Poll(); err != nil {
		t.Fatal(err)
	}

	got := drain(changes)
	// Агент, запущенный с вставленной флешкой, должен её увидеть.
	if len(got) != 2 || !got[0].Mounted || !got[1].Mounted {
		t.Fatalf("изменения: %+v", got)
	}
}

func TestLateSubscriberGetsReplayOfCurrentVolumes(t *testing.T) {
	provider := &fakeProvider{vols: []Volume{usb("E:", "A")}}
	hub := NewHub(provider, 0)
	hub.Poll()

	changes, cancel := hub.Subscribe()
	defer cancel()

	got := drain(changes)
	if len(got) != 1 || !got[0].Mounted || got[0].Volume.DriveLetter != "E:" {
		t.Fatalf("повтор: %+v", got)
	}
}

func TestEjectAndMediaSwapInTheSameLetter(t *testing.T) {
	provider := &fakeProvider{vols: []Volume{usb("E:", "A")}}
	hub := NewHub(provider, 0)
	changes, cancel := hub.Subscribe()
	defer cancel()
	hub.Poll()
	drain(changes)

	// Другая флешка в той же букве: это и unmount, и mount, а не тишина.
	provider.vols = []Volume{usb("E:", "B")}
	hub.Poll()
	got := drain(changes)
	if len(got) != 2 || got[0].Mounted || got[0].Volume.Serial != "A" || !got[1].Mounted || got[1].Volume.Serial != "B" {
		t.Fatalf("смена носителя: %+v", got)
	}

	provider.vols = nil
	hub.Poll()
	got = drain(changes)
	if len(got) != 1 || got[0].Mounted {
		t.Fatalf("извлечение: %+v", got)
	}
}

func TestFailedSnapshotKeepsKnownVolumes(t *testing.T) {
	provider := &fakeProvider{vols: []Volume{usb("E:", "A")}}
	hub := NewHub(provider, 0)
	changes, cancel := hub.Subscribe()
	defer cancel()
	hub.Poll()
	drain(changes)

	provider.err = errors.New("сбой")
	if err := hub.Poll(); err == nil {
		t.Fatal("ошибка снимка не возвращена")
	}

	// Сбой опроса не должен превращаться в «все носители извлечены».
	if got := drain(changes); len(got) != 0 {
		t.Fatalf("при сбое пришли изменения: %+v", got)
	}
	if len(hub.Current()) != 1 {
		t.Fatal("известные тома потеряны")
	}
}

func TestUnsubscribeClosesTheChannel(t *testing.T) {
	hub := NewHub(&fakeProvider{}, 0)
	changes, cancel := hub.Subscribe()

	cancel()
	cancel() // повторная отмена безопасна

	if _, ok := <-changes; ok {
		t.Fatal("канал не закрыт")
	}
	if err := hub.Poll(); err != nil {
		t.Fatal(err)
	}
}

func TestClassifyType(t *testing.T) {
	cases := []struct{ driveType, bus, want string }{
		{"removable", BusOther, TypeRemovable},
		{"removable", BusUSB, TypeRemovable},
		// Внешний жёсткий диск Windows показывает как фиксированный.
		{"fixed", BusUSB, TypeRemovable},
		{"fixed", BusOther, TypeFixed},
		{"fixed", BusUnknown, TypeFixed},
		{"remote", BusUnknown, TypeNetwork},
		{"cdrom", BusUnknown, TypeUnknown},
		{"", BusUnknown, TypeUnknown},
	}
	for _, c := range cases {
		if got := ClassifyType(c.driveType, c.bus); got != c.want {
			t.Errorf("ClassifyType(%q, %q) = %q, ожидалось %q", c.driveType, c.bus, got, c.want)
		}
	}
}
```

`agent/internal/volumes/descriptor_test.go`:

```go
package volumes

import (
	"encoding/binary"
	"testing"
)

// descriptor строит STORAGE_DEVICE_DESCRIPTOR с тремя строками за заголовком.
func descriptor(bus uint32, vendor, product, serial string) []byte {
	buf := make([]byte, 40)
	put := func(offsetField int, text string) {
		if text == "" {
			return
		}
		binary.LittleEndian.PutUint32(buf[offsetField:], uint32(len(buf)))
		buf = append(buf, []byte(text)...)
		buf = append(buf, 0)
	}
	binary.LittleEndian.PutUint32(buf[28:], bus)
	put(12, vendor)
	put(16, product)
	put(24, serial)
	return buf
}

func TestParseStorageDescriptorReadsBusAndStrings(t *testing.T) {
	bus, vendor, product, serial := ParseStorageDescriptor(descriptor(7, "Kingston ", "DataTraveler 3.0 ", " 0019E06B "))

	if bus != BusUSB || vendor != "Kingston" || product != "DataTraveler 3.0" || serial != "0019E06B" {
		t.Fatalf("bus=%q vendor=%q product=%q serial=%q", bus, vendor, product, serial)
	}
}

func TestParseStorageDescriptorMapsOtherBuses(t *testing.T) {
	if bus, _, _, _ := ParseStorageDescriptor(descriptor(11, "", "", "")); bus != BusOther {
		t.Fatalf("SATA = %q", bus)
	}
}

func TestParseStorageDescriptorSurvivesGarbage(t *testing.T) {
	for _, data := range [][]byte{nil, {1, 2, 3}, make([]byte, 40)} {
		bus, _, _, _ := ParseStorageDescriptor(data)
		if bus != BusUnknown && bus != BusOther {
			t.Errorf("мусор дал шину %q", bus)
		}
	}

	// Смещение строки за пределами буфера не должно давать панику.
	bad := make([]byte, 40)
	binary.LittleEndian.PutUint32(bad[12:], 9999)
	ParseStorageDescriptor(bad)
}
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `cd agent && go test ./internal/volumes/...`
Expected: FAIL (пакет без исходников).

- [ ] **Step 3: `agent/internal/volumes/volumes.go`**

```go
// Package volumes следит за подключёнными томами: сборщики usb и filewatch
// получают одни и те же изменения от одного опроса.
package volumes

import (
	"context"
	"log/slog"
	"sort"
	"sync"
	"time"

	"github.com/barysguard/agent/internal/events"
)

const (
	TypeFixed     = "fixed"
	TypeRemovable = "removable"
	TypeNetwork   = "network"
	TypeUnknown   = "unknown"

	BusUSB     = "usb"
	BusOther   = "other"
	BusUnknown = "unknown"
)

// subscriberBuffer хватает на повтор всех букв алфавита плюс запас на пачку изменений.
const subscriberBuffer = 256

type Volume struct {
	DriveLetter  string // "E:"
	Serial       string // серийный номер тома, hex
	Label, FS    string
	SizeBytes    int64
	Type         string
	Bus          string
	Vendor       string
	Product      string
	DeviceSerial string
}

// Key различает тома по паре «буква и серийный номер»: другая флешка
// в той же букве — это другой том.
func (v Volume) Key() string { return v.DriveLetter + "|" + v.Serial }

type Provider interface {
	Snapshot() ([]Volume, error)
}

type Change struct {
	Mounted bool
	Volume  Volume
}

// ClassifyType сводит тип диска Windows и шину к типу тома события.
// Внешним считается том на шине USB: внешние жёсткие диски Windows
// показывает как фиксированные, и по одному DRIVE_REMOVABLE их не поймать.
func ClassifyType(driveType, bus string) string {
	switch driveType {
	case "remote":
		return TypeNetwork
	case "removable":
		return TypeRemovable
	case "fixed":
		if bus == BusUSB {
			return TypeRemovable
		}
		return TypeFixed
	}
	return TypeUnknown
}

// diff отдаёт сначала извлечения, потом подключения: смена носителя в той же
// букве должна читаться как unmount и затем mount.
func diff(previous, current map[string]Volume) []Change {
	var out []Change
	for key, volume := range previous {
		if _, ok := current[key]; !ok {
			out = append(out, Change{Mounted: false, Volume: volume})
		}
	}
	for key, volume := range current {
		if _, ok := previous[key]; !ok {
			out = append(out, Change{Mounted: true, Volume: volume})
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Mounted != out[j].Mounted {
			return !out[i].Mounted
		}
		return out[i].Volume.Key() < out[j].Volume.Key()
	})
	return out
}

// Hub опрашивает провайдера и рассылает изменения подписчикам. Реализует
// events.Collector, чтобы запускаться вместе с остальными сборщиками;
// сам событий не порождает.
type Hub struct {
	provider Provider
	interval time.Duration

	mu    sync.Mutex
	known map[string]Volume
	subs  map[int]chan Change
	next  int
}

func NewHub(provider Provider, interval time.Duration) *Hub {
	return &Hub{provider: provider, interval: interval, known: map[string]Volume{}, subs: map[int]chan Change{}}
}

func (h *Hub) Name() string { return "volumes" }

// Subscribe подписывает на изменения. Подписчик сразу получает подключения
// для уже известных томов, поэтому порядок запуска сборщиков не важен.
func (h *Hub) Subscribe() (<-chan Change, func()) {
	h.mu.Lock()
	defer h.mu.Unlock()

	channel := make(chan Change, subscriberBuffer)
	id := h.next
	h.next++
	h.subs[id] = channel
	for _, volume := range sortedVolumes(h.known) {
		channel <- Change{Mounted: true, Volume: volume}
	}

	cancel := func() {
		h.mu.Lock()
		defer h.mu.Unlock()
		if existing, ok := h.subs[id]; ok {
			delete(h.subs, id)
			close(existing)
		}
	}
	return channel, cancel
}

func (h *Hub) Current() []Volume {
	h.mu.Lock()
	defer h.mu.Unlock()
	return sortedVolumes(h.known)
}

func sortedVolumes(known map[string]Volume) []Volume {
	out := make([]Volume, 0, len(known))
	for _, volume := range known {
		out = append(out, volume)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Key() < out[j].Key() })
	return out
}

// Poll выполняет один цикл опроса. Сбой снимка оставляет известные тома
// нетронутыми: иначе временная ошибка выглядела бы как извлечение всех носителей.
func (h *Hub) Poll() error {
	snapshot, err := h.provider.Snapshot()
	if err != nil {
		return err
	}
	current := make(map[string]Volume, len(snapshot))
	for _, volume := range snapshot {
		current[volume.Key()] = volume
	}

	h.mu.Lock()
	defer h.mu.Unlock()
	changes := diff(h.known, current)
	h.known = current
	for _, change := range changes {
		for id, channel := range h.subs {
			select {
			case channel <- change:
			default:
				// Медленный подписчик не должен останавливать остальных.
				slog.Warn("подписчик томов не успевает, изменение пропущено", "subscriber", id, "volume", change.Volume.Key())
			}
		}
	}
	return nil
}

func (h *Hub) Run(ctx context.Context, _ func(events.Envelope)) error {
	if err := h.Poll(); err != nil {
		slog.Warn("опрос томов не удался", "error", err)
	}
	if h.interval <= 0 {
		<-ctx.Done()
		return nil
	}
	ticker := time.NewTicker(h.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			if err := h.Poll(); err != nil {
				slog.Warn("опрос томов не удался", "error", err)
			}
		}
	}
}
```

- [ ] **Step 4: `agent/internal/volumes/descriptor.go`**

```go
package volumes

import (
	"encoding/binary"
	"strings"
)

// Шины STORAGE_BUS_TYPE, важные для классификации.
const busTypeUSB = 7

// ParseStorageDescriptor разбирает STORAGE_DEVICE_DESCRIPTOR, возвращённый
// IOCTL_STORAGE_QUERY_PROPERTY. Вынесено из Windows-кода, чтобы разбор
// проверялся на любой платформе и не паниковал на повреждённом ответе.
//
// Смещения: VendorIdOffset 12, ProductIdOffset 16, SerialNumberOffset 24,
// BusType 28; строки ASCII с завершающим нулём лежат дальше заголовка.
func ParseStorageDescriptor(data []byte) (bus, vendor, product, serial string) {
	if len(data) < 32 {
		return BusUnknown, "", "", ""
	}
	bus = BusOther
	if binary.LittleEndian.Uint32(data[28:32]) == busTypeUSB {
		bus = BusUSB
	}
	read := func(field int) string {
		offset := int(binary.LittleEndian.Uint32(data[field : field+4]))
		if offset <= 0 || offset >= len(data) {
			return ""
		}
		end := offset
		for end < len(data) && data[end] != 0 {
			end++
		}
		return strings.TrimSpace(string(data[offset:end]))
	}
	return bus, read(12), read(16), read(24)
}
```

- [ ] **Step 5: Запустить тесты**

Run: `cd agent && gofmt -l internal/volumes; go vet ./internal/volumes/... && go test ./internal/volumes/... -count=1 -race`
Expected: PASS. (`-race` на Windows требует cgo; если недоступен, без него.)

- [ ] **Step 6: Коммит**

```bash
git add agent/internal/volumes
git commit -m "feat(agent): volumes hub with replay, type classification and storage descriptor parser" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Провайдер томов Windows

**Files:**
- Create: `agent/internal/volumes/provider_windows.go`, `agent/internal/volumes/provider_other.go`
- Test: `agent/internal/volumes/provider_windows_test.go`

**Interfaces:**
- Consumes: `Volume`, `ClassifyType`, `ParseStorageDescriptor` (задача 2).
- Produces: `func NewProvider() Provider` (Windows — реальные тома; прочие — пустой список).

- [ ] **Step 1: Падающий тест `provider_windows_test.go`**

```go
//go:build windows

package volumes

import (
	"os"
	"strings"
	"testing"
)

func TestSnapshotContainsTheSystemDrive(t *testing.T) {
	snapshot, err := NewProvider().Snapshot()
	if err != nil {
		t.Fatalf("Snapshot: %v", err)
	}

	system := strings.ToUpper(os.Getenv("SystemDrive"))
	for _, volume := range snapshot {
		if volume.DriveLetter == system {
			if volume.SizeBytes <= 0 || volume.FS == "" {
				t.Fatalf("системный том описан неполно: %+v", volume)
			}
			if volume.Type != TypeFixed && volume.Type != TypeRemovable {
				t.Fatalf("тип системного тома %q", volume.Type)
			}
			return
		}
	}
	t.Fatalf("системный диск %s не найден среди %d томов", system, len(snapshot))
}

func TestSnapshotReportsLettersInDriveFormat(t *testing.T) {
	snapshot, _ := NewProvider().Snapshot()
	for _, volume := range snapshot {
		if len(volume.DriveLetter) != 2 || volume.DriveLetter[1] != ':' {
			t.Fatalf("буква %q не в формате «C:»", volume.DriveLetter)
		}
	}
}
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `cd agent && go test ./internal/volumes/... 2>&1 | head -5`
Expected: FAIL (`undefined: NewProvider`).

- [ ] **Step 3: `provider_other.go`**

```go
//go:build !windows

package volumes

type emptyProvider struct{}

func (emptyProvider) Snapshot() ([]Volume, error) { return nil, nil }

// NewProvider на платформах без поддержки отдаёт пустой список томов:
// сборщики там не запускаются, но агент обязан собираться.
func NewProvider() Provider { return emptyProvider{} }
```

- [ ] **Step 4: `provider_windows.go`**

```go
//go:build windows

package volumes

import (
	"fmt"
	"unsafe"

	"golang.org/x/sys/windows"
)

const (
	ioctlStorageQueryProperty = 0x002D1400
	storageDeviceProperty     = 0
	propertyStandardQuery     = 0
)

// storagePropertyQuery — STORAGE_PROPERTY_QUERY; размер 12 байт, как в C.
type storagePropertyQuery struct {
	PropertyID           uint32
	QueryType            uint32
	AdditionalParameters [1]byte
}

type winProvider struct{}

func NewProvider() Provider { return winProvider{} }

func driveTypeName(driveType uint32) string {
	switch driveType {
	case windows.DRIVE_REMOVABLE:
		return "removable"
	case windows.DRIVE_FIXED:
		return "fixed"
	case windows.DRIVE_REMOTE:
		return "remote"
	case windows.DRIVE_CDROM:
		return "cdrom"
	}
	return ""
}

func (winProvider) Snapshot() ([]Volume, error) {
	mask, err := windows.GetLogicalDrives()
	if err != nil {
		return nil, fmt.Errorf("GetLogicalDrives: %w", err)
	}

	var volumes []Volume
	for i := 0; i < 26; i++ {
		if mask&(1<<uint(i)) == 0 {
			continue
		}
		letter := string(rune('A'+i)) + ":"
		root, err := windows.UTF16PtrFromString(letter + `\`)
		if err != nil {
			continue
		}

		driveType := driveTypeName(windows.GetDriveType(root))
		if driveType == "" || driveType == "cdrom" {
			continue
		}

		label := make([]uint16, 261)
		fsName := make([]uint16, 261)
		var serial uint32
		// Не готовый к чтению том (пустой картридер) пропускается: данных
		// о нём нет, и он появится при вставке носителя.
		if err := windows.GetVolumeInformation(root, &label[0], uint32(len(label)), &serial, nil, nil, &fsName[0], uint32(len(fsName))); err != nil {
			continue
		}

		volume := Volume{
			DriveLetter: letter,
			Serial:      fmt.Sprintf("%04X-%04X", serial>>16, serial&0xFFFF),
			Label:       windows.UTF16ToString(label),
			FS:          windows.UTF16ToString(fsName),
			Bus:         BusUnknown,
		}

		var free, total, totalFree uint64
		if err := windows.GetDiskFreeSpaceEx(root, &free, &total, &totalFree); err == nil {
			volume.SizeBytes = int64(total)
		}

		if driveType != "remote" {
			bus, vendor, product, deviceSerial := queryStorage(letter)
			volume.Bus, volume.Vendor, volume.Product, volume.DeviceSerial = bus, vendor, product, deviceSerial
		}
		volume.Type = ClassifyType(driveType, volume.Bus)
		volumes = append(volumes, volume)
	}
	return volumes, nil
}

// queryStorage спрашивает у устройства шину и описание. Открытие тома без прав
// на чтение не требует администратора; при отказе возвращается «неизвестно»,
// и том классифицируется только по типу диска.
func queryStorage(letter string) (bus, vendor, product, serial string) {
	path, err := windows.UTF16PtrFromString(`\\.\` + letter)
	if err != nil {
		return BusUnknown, "", "", ""
	}
	handle, err := windows.CreateFile(path, 0, windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE,
		nil, windows.OPEN_EXISTING, 0, 0)
	if err != nil {
		return BusUnknown, "", "", ""
	}
	defer windows.CloseHandle(handle)

	query := storagePropertyQuery{PropertyID: storageDeviceProperty, QueryType: propertyStandardQuery}
	out := make([]byte, 1024)
	var returned uint32
	err = windows.DeviceIoControl(handle, ioctlStorageQueryProperty,
		(*byte)(unsafe.Pointer(&query)), uint32(unsafe.Sizeof(query)),
		&out[0], uint32(len(out)), &returned, nil)
	if err != nil {
		return BusUnknown, "", "", ""
	}
	return ParseStorageDescriptor(out[:returned])
}
```

- [ ] **Step 5: Запустить тесты и сборку под Linux**

```bash
cd agent && gofmt -l internal/volumes; go vet ./internal/volumes/... && go test ./internal/volumes/... -count=1
GOOS=linux go build ./... && echo linux-build-ok
```

Expected: PASS; сборка под Linux проходит.

- [ ] **Step 6: Коммит**

```bash
git add agent/internal/volumes
git commit -m "feat(agent): Windows volume provider with USB bus detection" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Сборщик `usb` и пакет `identity`

**Files:**
- Create: `agent/internal/collectors/usb/usb.go`, `agent/internal/identity/identity.go`, `identity_windows.go`, `identity_other.go`
- Test: `agent/internal/collectors/usb/usb_test.go`, `agent/internal/identity/identity_test.go`, `identity_windows_test.go`

**Interfaces:**
- Consumes: `volumes.Hub`, `volumes.Change` (задача 2).
- Produces: `usb.New(hub *volumes.Hub, consoleUser func() map[string]any) *Collector` (`events.Collector`); `usb.BuildEvent(change volumes.Change, actor map[string]any) (events.Envelope, error)`; `identity.Resolver` (`FileOwner(path string) map[string]any`, `ConsoleUser() map[string]any`); `identity.New() Resolver`; `identity.IsServiceSID(sid string) bool`; `identity.Pick(owner, console map[string]any) map[string]any`. Актёр — `{"user_sid": string, "user_name": string, "session_id": int?}`.

- [ ] **Step 1: Падающие тесты `agent/internal/collectors/usb/usb_test.go`**

```go
package usb_test

import (
	"context"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/collectors/usb"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

type provider struct{ vols []volumes.Volume }

func (p *provider) Snapshot() ([]volumes.Volume, error) { return p.vols, nil }

var flash = volumes.Volume{
	DriveLetter: "E:", Serial: "0781-5583", Label: "KINGSTON", FS: "FAT32", SizeBytes: 32015679488,
	Type: volumes.TypeRemovable, Bus: volumes.BusUSB, Vendor: "Kingston", Product: "DataTraveler 3.0", DeviceSerial: "0019E06B",
}

func TestBuildEventDescribesTheDevice(t *testing.T) {
	env, err := usb.BuildEvent(volumes.Change{Mounted: true, Volume: flash}, map[string]any{"user_name": "PC\\ivanov"})
	if err != nil {
		t.Fatal(err)
	}

	if env.Channel != "usb" || env.Action != "mount" || env.SeverityHint != events.SeverityLow {
		t.Fatalf("конверт: %+v", env)
	}
	if env.Subject["drive_letter"] != "E:" || env.Subject["size_bytes"] != int64(32015679488) {
		t.Fatalf("subject: %+v", env.Subject)
	}
	volume := env.Subject["volume"].(map[string]any)
	device := env.Subject["device"].(map[string]any)
	if volume["type"] != "removable" || volume["serial"] != "0781-5583" || volume["fs"] != "FAT32" {
		t.Errorf("volume: %+v", volume)
	}
	if device["bus"] != "usb" || device["vendor"] != "Kingston" || device["product"] != "DataTraveler 3.0" || device["serial"] != "0019E06B" {
		t.Errorf("device: %+v", device)
	}
	if env.Actor["user_name"] != "PC\\ivanov" {
		t.Errorf("actor: %+v", env.Actor)
	}

	off, _ := usb.BuildEvent(volumes.Change{Mounted: false, Volume: flash}, nil)
	if off.Action != "unmount" || off.SeverityHint != events.SeverityInfo {
		t.Errorf("unmount: %+v", off)
	}
}

func TestOnlyUSBVolumesProduceEvents(t *testing.T) {
	provider := &provider{vols: []volumes.Volume{
		flash,
		{DriveLetter: "C:", Serial: "AAAA", Type: volumes.TypeFixed, Bus: volumes.BusOther},
	}}
	hub := volumes.NewHub(provider, 0)
	collector := usb.New(hub, func() map[string]any { return map[string]any{"user_name": "u"} })

	var got []events.Envelope
	emitted := make(chan struct{}, 8)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		collector.Run(ctx, func(e events.Envelope) { got = append(got, e); emitted <- struct{}{} })
		close(done)
	}()

	time.Sleep(20 * time.Millisecond) // подписка до опроса
	hub.Poll()
	select {
	case <-emitted:
	case <-time.After(2 * time.Second):
		t.Fatal("событие подключения не пришло")
	}
	provider.vols = nil
	hub.Poll()
	select {
	case <-emitted:
	case <-time.After(2 * time.Second):
		t.Fatal("событие отключения не пришло")
	}
	cancel()
	<-done

	if len(got) != 2 || got[0].Action != "mount" || got[1].Action != "unmount" {
		t.Fatalf("события: %+v", got)
	}
	if got[0].Actor["user_name"] != "u" {
		t.Errorf("актёр не подставлен: %+v", got[0].Actor)
	}
}

func TestStopsOnContextCancel(t *testing.T) {
	collector := usb.New(volumes.NewHub(&provider{}, 0), nil)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- collector.Run(ctx, func(events.Envelope) {}) }()

	cancel()

	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("сборщик не остановился")
	}
	if collector.Name() != "usb" {
		t.Fatalf("Name = %q", collector.Name())
	}
}
```

`agent/internal/identity/identity_test.go`:

```go
package identity

import "testing"

func TestIsServiceSID(t *testing.T) {
	service := []string{"S-1-5-18", "S-1-5-32-544", "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464", "S-1-5-19", "S-1-5-20"}
	for _, sid := range service {
		if !IsServiceSID(sid) {
			t.Errorf("%s должен считаться служебным", sid)
		}
	}
	user := []string{"S-1-5-21-1004336348-1177238915-682003330-1013", "S-1-12-1-111-222-333-444", ""}
	for _, sid := range user {
		if IsServiceSID(sid) {
			t.Errorf("%q не служебный", sid)
		}
	}
}

func TestPickPrefersARealOwnerOverTheConsoleUser(t *testing.T) {
	owner := map[string]any{"user_sid": "S-1-5-21-1-2-3-1001", "user_name": "PC\\owner"}
	console := map[string]any{"user_sid": "S-1-5-21-1-2-3-1002", "user_name": "PC\\console"}

	if got := Pick(owner, console); got["user_name"] != "PC\\owner" {
		t.Fatalf("выбран %v", got)
	}
}

func TestPickFallsBackToConsoleForServiceOrUnknownOwner(t *testing.T) {
	service := map[string]any{"user_sid": "S-1-5-18", "user_name": "NT AUTHORITY\\SYSTEM"}
	console := map[string]any{"user_sid": "S-1-5-21-1-2-3-1002", "user_name": "PC\\console"}

	if got := Pick(service, console); got["user_name"] != "PC\\console" {
		t.Fatalf("для SYSTEM выбран %v", got)
	}
	if got := Pick(nil, console); got["user_name"] != "PC\\console" {
		t.Fatalf("без владельца выбран %v", got)
	}
	if got := Pick(nil, nil); got != nil {
		t.Fatalf("без данных выбран %v", got)
	}
	// Служебный владелец лучше пустоты: хотя бы видно, что файл системный.
	if got := Pick(service, nil); got["user_name"] != "NT AUTHORITY\\SYSTEM" {
		t.Fatalf("без консоли выбран %v", got)
	}
}
```

`agent/internal/identity/identity_windows_test.go`:

```go
//go:build windows

package identity

import (
	"os"
	"path/filepath"
	"testing"
)

func TestFileOwnerOfAFileWeCreated(t *testing.T) {
	path := filepath.Join(t.TempDir(), "owned.txt")
	if err := os.WriteFile(path, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}

	owner := New().FileOwner(path)

	if owner == nil {
		t.Fatal("владелец не определён")
	}
	sid, _ := owner["user_sid"].(string)
	name, _ := owner["user_name"].(string)
	if len(sid) < 8 || sid[:4] != "S-1-" || name == "" {
		t.Fatalf("владелец: %+v", owner)
	}
}

func TestFileOwnerOfMissingFileIsNil(t *testing.T) {
	if New().FileOwner(filepath.Join(t.TempDir(), "нет-такого.txt")) != nil {
		t.Fatal("для несуществующего файла вернулся владелец")
	}
}

func TestConsoleUserDoesNotPanic(t *testing.T) {
	// На машине без интерактивной сессии (сервер сборки) пользователя нет, и это не ошибка.
	if user := New().ConsoleUser(); user != nil {
		if user["user_name"] == "" {
			t.Fatalf("пустое имя: %+v", user)
		}
	}
}
```

- [ ] **Step 2: Запустить, убедиться, что падает**

Run: `cd agent && go test ./internal/collectors/usb/... ./internal/identity/... 2>&1 | head -6`
Expected: FAIL (пакеты без исходников).

- [ ] **Step 3: `agent/internal/identity/identity.go`**

```go
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
```

- [ ] **Step 4: `identity_other.go`**

```go
//go:build !windows

package identity

type none struct{}

func (none) FileOwner(string) map[string]any { return nil }
func (none) ConsoleUser() map[string]any     { return nil }

// New на платформах без поддержки не определяет никого.
func New() Resolver { return none{} }
```

- [ ] **Step 5: `identity_windows.go`**

```go
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
	wtsapi            = windows.NewLazySystemDLL("wtsapi32.dll")
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
```

После вставки выровняйте имена в блоке `var (` через `gofmt -w`.

- [ ] **Step 6: `agent/internal/collectors/usb/usb.go`**

```go
// Package usb — сборщик канала usb: подключение и отключение внешних носителей.
package usb

import (
	"context"
	"log/slog"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

type Collector struct {
	hub         *volumes.Hub
	consoleUser func() map[string]any
}

// New. consoleUser может быть nil: тогда актёр не заполняется.
func New(hub *volumes.Hub, consoleUser func() map[string]any) *Collector {
	return &Collector{hub: hub, consoleUser: consoleUser}
}

func (c *Collector) Name() string { return "usb" }

// Run события порождает только для томов на шине USB: внутренние диски
// и сетевые диски в канал usb не попадают.
func (c *Collector) Run(ctx context.Context, emit func(events.Envelope)) error {
	changes, cancel := c.hub.Subscribe()
	defer cancel()

	for {
		select {
		case <-ctx.Done():
			return nil
		case change, ok := <-changes:
			if !ok {
				return nil
			}
			if change.Volume.Bus != volumes.BusUSB {
				continue
			}
			var actor map[string]any
			if c.consoleUser != nil {
				actor = c.consoleUser()
			}
			env, err := BuildEvent(change, actor)
			if err != nil {
				slog.Error("событие usb не создано", "volume", change.Volume.Key(), "error", err)
				continue
			}
			emit(env)
		}
	}
}

// BuildEvent описывает подключение или отключение носителя (раздел 7.2 спеки).
func BuildEvent(change volumes.Change, actor map[string]any) (events.Envelope, error) {
	action, severity := "unmount", events.SeverityInfo
	if change.Mounted {
		action, severity = "mount", events.SeverityLow
	}
	v := change.Volume
	env, err := events.NewEnvelope(events.ChannelUSB, action, severity, map[string]any{
		"drive_letter": v.DriveLetter,
		"size_bytes":   v.SizeBytes,
		"volume": map[string]any{
			"type": v.Type, "serial": v.Serial, "label": v.Label, "fs": v.FS,
		},
		"device": map[string]any{
			"bus": v.Bus, "vendor": v.Vendor, "product": v.Product, "serial": v.DeviceSerial,
		},
	})
	if err != nil {
		return events.Envelope{}, err
	}
	env.Actor = actor
	return env, nil
}
```

- [ ] **Step 7: Запустить тесты**

```bash
cd agent && gofmt -w internal/identity && gofmt -l internal; go vet ./internal/... && go test ./internal/collectors/... ./internal/identity/... -count=1 && GOOS=linux go build ./... && echo linux-ok
```

Expected: PASS, сборка под Linux проходит.

- [ ] **Step 8: Коммит**

```bash
git add agent/internal/collectors agent/internal/identity
git commit -m "feat(agent): usb collector and identity resolver (file owner, console user)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Ядро `filewatch` — конфигурация, склейка, хеширование, индекс, событие, конвейер

**Files:**
- Create: `agent/internal/collectors/filewatch/` — `config.go`, `exclude.go`, `debounce.go`, `hashindex.go`, `limiter.go`, `hasher.go`, `volumeof.go`, `event.go`, `pipeline.go`, `notify.go`
- Test: в том же каталоге — `config_test.go`, `exclude_test.go`, `debounce_test.go`, `hashindex_test.go`, `limiter_test.go`, `hasher_test.go`, `volumeof_test.go`, `event_test.go`, `pipeline_test.go`, `notify_test.go`

**Interfaces:**
- Consumes: `volumes.Volume` (задача 2), `identity.Resolver`, `identity.Pick` (задача 4), `events.*`.
- Produces (пакет `filewatch`):
  - `type Config struct{Enabled bool; Paths, Exclude []string; Stable, MaxWait time.Duration; MaxHashBytes int64; MaxEventsPerSecond int}`; `ConfigFromDocument(doc map[string]any, profiles []string, dataDir string) Config`; `ProfileDirs(usersRoot string) []string`;
  - `type Excluder`; `NewExcluder(patterns []string) *Excluder`; `(*Excluder).Match(path string) bool`;
  - `type Op int` (`OpCreate|OpModify|OpDelete`); `type Action string` (`ActionCreate|ActionModify|ActionDelete|ActionRename|ActionCopy`); `type Settled struct{Path, OldPath string; Action Action}`; `NewDebouncer() *Debouncer`; `Notify(path string, op Op, now time.Time)`; `NotifyRename(oldPath, newPath string, now time.Time)`; `Settle(now time.Time, stable, maxWait time.Duration) []Settled`;
  - `NewHashIndex(max int, ttl time.Duration) *HashIndex`; `Put(sha string, size int64, path string, now time.Time)`; `Lookup(sha string, size int64, now time.Time) (string, bool)`;
  - `NewLimiter(perSecond int) *Limiter`; `Allow(now time.Time) bool`;
  - `type HashResult struct{SHA256 string; Size int64; Status string}`; статусы `HashOK|HashSkippedSize|HashUnavailable|HashGone`; `type Hasher struct{Open func(path string) (io.ReadCloser, int64, error); Sleep func(time.Duration); Now func() time.Time}`; `NewHasher() Hasher`; `(Hasher).Hash(path string, maxBytes int64, deadline time.Time) HashResult`;
  - `VolumeFor(path string, vols []volumes.Volume) volumes.Volume`;
  - `type EventInput`; `BuildEvent(in EventInput) (events.Envelope, error)`;
  - `type Kind int` (`Created|Modified|Deleted|Renamed`); `type Raw struct{Kind Kind; Path, OldPath string}`; `ParseNotifications(root string, buf []byte) []Raw`;
  - `type Attributor interface{Attribute(path string) (map[string]any, bool)}`;
  - `type PipelineDeps struct{Config Config; Volumes func() []volumes.Volume; Hasher Hasher; Identity identity.Resolver; Attributor Attributor; Emit func(events.Envelope); Now func() time.Time}`; `NewPipeline(PipelineDeps) *Pipeline`; `(*Pipeline).Handle(Raw)`; `(*Pipeline).Tick()`.

Задача большая, но состоит из независимых маленьких блоков; каждый — свой тест и свой фрагмент кода. Коммитьте по блокам (шаги ниже), финальный `task-done` — по всему пакету.

- [ ] **Step 1: Конфигурация — падающие тесты `config_test.go`**

```go
package filewatch

import (
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"
)

func TestDefaultsWhenDocumentHasNoCollectors(t *testing.T) {
	cfg := ConfigFromDocument(nil, []string{`C:\Users\ivanov`}, `C:\ProgramData\BarysGuard`)

	if !cfg.Enabled || cfg.Stable != 1500*time.Millisecond || cfg.MaxWait != 30*time.Second {
		t.Fatalf("умолчания: %+v", cfg)
	}
	if cfg.MaxHashBytes != 256*1024*1024 || cfg.MaxEventsPerSecond != 200 {
		t.Fatalf("умолчания: %+v", cfg)
	}
	want := []string{`C:\Users\ivanov\Documents`, `C:\Users\ivanov\Desktop`, `C:\Users\ivanov\Downloads`}
	if !reflect.DeepEqual(cfg.Paths, want) {
		t.Fatalf("пути: %v", cfg.Paths)
	}
}

func TestUsersTokenExpandsForEveryProfile(t *testing.T) {
	doc := map[string]any{"collectors": map[string]any{"file_watch": map[string]any{
		"paths": []any{`%USERS%\Documents`, `D:\Shared`},
	}}}

	cfg := ConfigFromDocument(doc, []string{`C:\Users\a`, `C:\Users\b`}, "")

	want := []string{`C:\Users\a\Documents`, `C:\Users\b\Documents`, `D:\Shared`}
	if !reflect.DeepEqual(cfg.Paths, want) {
		t.Fatalf("пути: %v", cfg.Paths)
	}
}

func TestDocumentValuesOverrideDefaultsAndGarbageFallsBack(t *testing.T) {
	doc := map[string]any{"collectors": map[string]any{"file_watch": map[string]any{
		"enabled": false, "stable_ms": float64(500), "max_hash_bytes": "много", "max_events_per_second": float64(-5),
	}}}

	cfg := ConfigFromDocument(doc, nil, "")

	if cfg.Enabled || cfg.Stable != 500*time.Millisecond {
		t.Fatalf("значения документа: %+v", cfg)
	}
	if cfg.MaxHashBytes != 256*1024*1024 || cfg.MaxEventsPerSecond != 200 {
		t.Fatalf("мусор должен давать умолчание: %+v", cfg)
	}
}

func TestDataDirIsAlwaysExcluded(t *testing.T) {
	cfg := ConfigFromDocument(nil, nil, `C:\ProgramData\BarysGuard`)

	found := false
	for _, pattern := range cfg.Exclude {
		if pattern == `C:\ProgramData\BarysGuard\*` {
			found = true
		}
	}
	if !found {
		t.Fatalf("каталог данных агента не исключён: %v", cfg.Exclude)
	}
}

func TestProfileDirsSkipsSystemProfiles(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"ivanov", "petrov", "Public", "Default", "Default User", "All Users"} {
		os.Mkdir(filepath.Join(root, name), 0o755)
	}
	os.WriteFile(filepath.Join(root, "desktop.ini"), nil, 0o644)

	got := ProfileDirs(root)

	sort.Strings(got)
	want := []string{filepath.Join(root, "ivanov"), filepath.Join(root, "petrov")}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("профили: %v", got)
	}
}
```

- [ ] **Step 2: Исключения — падающие тесты `exclude_test.go`**

```go
package filewatch

import "testing"

func TestExcluderMatchesWindowsStyleGlobs(t *testing.T) {
	excluder := NewExcluder([]string{`*\~$*`, `*.tmp`, `*.crdownload`, `*\AppData\*`, `C:\ProgramData\BarysGuard\*`})

	hits := []string{
		`C:\Users\u\Documents\~$report.docx`,
		`C:\Users\u\Downloads\file.TMP`,
		`C:\Users\u\Downloads\movie.mkv.crdownload`,
		`C:\Users\u\appdata\Local\x.dat`,
		`C:\ProgramData\BarysGuard\events.db`,
	}
	for _, path := range hits {
		if !excluder.Match(path) {
			t.Errorf("%q должен исключаться", path)
		}
	}
	misses := []string{
		`C:\Users\u\Documents\отчёт.docx`,
		`E:\report.xlsx`,
		`C:\Users\u\Documents\tmp-notes.txt`,
	}
	for _, path := range misses {
		if excluder.Match(path) {
			t.Errorf("%q не должен исключаться", path)
		}
	}
}

func TestEmptyExcluderMatchesNothing(t *testing.T) {
	if NewExcluder(nil).Match(`C:\x`) {
		t.Fatal("пустой список ничего не исключает")
	}
}

func TestQuestionMarkMatchesOneCharacter(t *testing.T) {
	excluder := NewExcluder([]string{`*.t?t`})
	if !excluder.Match(`C:\a.txt`) || excluder.Match(`C:\a.tt`) {
		t.Fatal("? должен совпадать ровно с одним символом")
	}
}
```

- [ ] **Step 3: Склейка — падающие тесты `debounce_test.go`**

```go
package filewatch

import (
	"reflect"
	"testing"
	"time"
)

var t0 = time.Date(2026, 10, 5, 10, 0, 0, 0, time.UTC)

const (
	stable  = 1500 * time.Millisecond
	maxWait = 30 * time.Second
)

func at(ms int) time.Time { return t0.Add(time.Duration(ms) * time.Millisecond) }

func TestNothingSettlesWhileNotificationsKeepComing(t *testing.T) {
	d := NewDebouncer()
	d.Notify(`E:\a.bin`, OpCreate, at(0))
	d.Notify(`E:\a.bin`, OpModify, at(1000))
	d.Notify(`E:\a.bin`, OpModify, at(2000))

	if got := d.Settle(at(3000), stable, maxWait); len(got) != 0 {
		t.Fatalf("рано: %+v", got)
	}
	got := d.Settle(at(3600), stable, maxWait)
	if len(got) != 1 || got[0].Action != ActionCreate || got[0].Path != `E:\a.bin` {
		t.Fatalf("итог: %+v", got)
	}
	if again := d.Settle(at(9000), stable, maxWait); len(again) != 0 {
		t.Fatalf("событие вышло дважды: %+v", again)
	}
}

func TestMaxWaitForcesTheEventOutOfAnEndlessWrite(t *testing.T) {
	d := NewDebouncer()
	for ms := 0; ms <= 31000; ms += 1000 {
		d.Notify(`E:\big.iso`, OpModify, at(ms))
	}

	got := d.Settle(at(31000), stable, maxWait)

	if len(got) != 1 || got[0].Action != ActionModify {
		t.Fatalf("итог: %+v", got)
	}
}

func TestCreateThenDeleteInOneWindowProducesNothing(t *testing.T) {
	d := NewDebouncer()
	d.Notify(`C:\t\scratch.dat`, OpCreate, at(0))
	d.Notify(`C:\t\scratch.dat`, OpDelete, at(100))

	if got := d.Settle(at(5000), stable, maxWait); len(got) != 0 {
		t.Fatalf("временный файл дал событие: %+v", got)
	}
}

func TestDeleteOfAnExistingFile(t *testing.T) {
	d := NewDebouncer()
	d.Notify(`C:\d\old.txt`, OpDelete, at(0))

	got := d.Settle(at(2000), stable, maxWait)

	if len(got) != 1 || got[0].Action != ActionDelete {
		t.Fatalf("итог: %+v", got)
	}
}

func TestDeleteThenCreateIsAReplacementNotACreate(t *testing.T) {
	// Так сохраняют файлы редакторы: старый удаляется, новый создаётся.
	d := NewDebouncer()
	d.Notify(`C:\d\doc.docx`, OpDelete, at(0))
	d.Notify(`C:\d\doc.docx`, OpCreate, at(50))

	got := d.Settle(at(2000), stable, maxWait)

	if len(got) != 1 || got[0].Action != ActionModify {
		t.Fatalf("итог: %+v", got)
	}
}

func TestRenameKeepsBothPaths(t *testing.T) {
	d := NewDebouncer()
	d.NotifyRename(`E:\old.xlsx`, `E:\new.xlsx`, at(0))

	got := d.Settle(at(2000), stable, maxWait)

	want := []Settled{{Path: `E:\new.xlsx`, OldPath: `E:\old.xlsx`, Action: ActionRename}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("итог: %+v", got)
	}
}

func TestRenameOfAFileCreatedInTheSameWindowIsACreate(t *testing.T) {
	// Word пишет ~WRD0001.tmp и переименовывает его в документ.
	d := NewDebouncer()
	d.Notify(`C:\d\~WRD0001.tmp`, OpCreate, at(0))
	d.NotifyRename(`C:\d\~WRD0001.tmp`, `C:\d\report.docx`, at(100))

	got := d.Settle(at(2000), stable, maxWait)

	if len(got) != 1 || got[0].Action != ActionCreate || got[0].Path != `C:\d\report.docx` || got[0].OldPath != "" {
		t.Fatalf("итог: %+v", got)
	}
}

func TestPathsAreCaseInsensitiveButKeepTheirSpelling(t *testing.T) {
	d := NewDebouncer()
	d.Notify(`C:\Docs\Отчёт.docx`, OpCreate, at(0))
	d.Notify(`c:\docs\отчёт.DOCX`, OpModify, at(10))

	got := d.Settle(at(2000), stable, maxWait)

	if len(got) != 1 {
		t.Fatalf("регистр размножил события: %+v", got)
	}
}

func TestSettledOrderIsDeterministic(t *testing.T) {
	d := NewDebouncer()
	d.Notify(`E:\b`, OpCreate, at(10))
	d.Notify(`E:\a`, OpCreate, at(0))

	got := d.Settle(at(5000), stable, maxWait)

	if len(got) != 2 || got[0].Path != `E:\a` || got[1].Path != `E:\b` {
		t.Fatalf("порядок: %+v", got)
	}
}
```

- [ ] **Step 4: Индекс, ограничитель, хеширование — падающие тесты**

`hashindex_test.go`:

```go
package filewatch

import (
	"fmt"
	"testing"
	"time"
)

func TestIndexFindsByHashAndSize(t *testing.T) {
	idx := NewHashIndex(10, time.Hour)
	idx.Put("aa", 100, `C:\Users\u\Documents\a.docx`, t0)

	if path, ok := idx.Lookup("aa", 100, t0.Add(time.Minute)); !ok || path != `C:\Users\u\Documents\a.docx` {
		t.Fatalf("lookup: %q %v", path, ok)
	}
	if _, ok := idx.Lookup("aa", 101, t0); ok {
		t.Fatal("другой размер не должен совпадать")
	}
	if _, ok := idx.Lookup("bb", 100, t0); ok {
		t.Fatal("другой хеш не должен совпадать")
	}
}

func TestIndexEntriesExpire(t *testing.T) {
	idx := NewHashIndex(10, time.Hour)
	idx.Put("aa", 1, `C:\a`, t0)

	if _, ok := idx.Lookup("aa", 1, t0.Add(2*time.Hour)); ok {
		t.Fatal("просроченная запись найдена")
	}
}

func TestIndexEvictsTheLeastRecentlyUsed(t *testing.T) {
	idx := NewHashIndex(3, time.Hour)
	for i := 0; i < 3; i++ {
		idx.Put(fmt.Sprint("h", i), 1, fmt.Sprint(`C:\f`, i), t0)
	}
	idx.Lookup("h0", 1, t0) // h0 стал свежим, вытеснится h1
	idx.Put("h3", 1, `C:\f3`, t0)

	if _, ok := idx.Lookup("h1", 1, t0); ok {
		t.Fatal("h1 должен был вытесниться")
	}
	for _, key := range []string{"h0", "h2", "h3"} {
		if _, ok := idx.Lookup(key, 1, t0); !ok {
			t.Fatalf("%s вытеснен напрасно", key)
		}
	}
}

func TestPutOfTheSameContentUpdatesThePath(t *testing.T) {
	idx := NewHashIndex(3, time.Hour)
	idx.Put("aa", 1, `C:\old`, t0)
	idx.Put("aa", 1, `C:\new`, t0.Add(time.Second))

	if path, _ := idx.Lookup("aa", 1, t0.Add(2*time.Second)); path != `C:\new` {
		t.Fatalf("путь: %q", path)
	}
}
```

`limiter_test.go`:

```go
package filewatch

import (
	"testing"
	"time"
)

func TestLimiterAllowsPerSecondBudgetThenRefills(t *testing.T) {
	limiter := NewLimiter(3)
	allowed := 0
	for i := 0; i < 10; i++ {
		if limiter.Allow(t0) {
			allowed++
		}
	}
	if allowed != 3 {
		t.Fatalf("разрешено %d, ожидалось 3", allowed)
	}
	if !limiter.Allow(t0.Add(1100 * time.Millisecond)) {
		t.Fatal("после секунды бюджет должен восстановиться")
	}
}
```

`hasher_test.go`:

```go
package filewatch

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func memoryOpen(files map[string]string) func(string) (io.ReadCloser, int64, error) {
	return func(path string) (io.ReadCloser, int64, error) {
		content, ok := files[path]
		if !ok {
			return nil, 0, os.ErrNotExist
		}
		return io.NopCloser(strings.NewReader(content)), int64(len(content)), nil
	}
}

func TestHashOfAnOrdinaryFile(t *testing.T) {
	hasher := Hasher{Open: memoryOpen(map[string]string{`E:\a.txt`: "содержимое"}), Sleep: func(time.Duration) {}, Now: time.Now}

	result := hasher.Hash(`E:\a.txt`, 1<<20, time.Now().Add(time.Second))

	sum := sha256.Sum256([]byte("содержимое"))
	if result.Status != HashOK || result.SHA256 != hex.EncodeToString(sum[:]) || result.Size != int64(len("содержимое")) {
		t.Fatalf("результат: %+v", result)
	}
}

func TestFileLargerThanTheLimitIsSkippedWithoutReading(t *testing.T) {
	read := false
	hasher := Hasher{
		Open: func(string) (io.ReadCloser, int64, error) {
			return readerFunc(func([]byte) (int, error) { read = true; return 0, io.EOF }), 5 << 30, nil
		},
		Sleep: func(time.Duration) {}, Now: time.Now,
	}

	result := hasher.Hash(`E:\huge.iso`, 256<<20, time.Now().Add(time.Second))

	if result.Status != HashSkippedSize || result.Size != 5<<30 || read {
		t.Fatalf("результат: %+v, читали: %v", result, read)
	}
}

type readerFunc func([]byte) (int, error)

func (f readerFunc) Read(p []byte) (int, error) { return f(p) }
func (readerFunc) Close() error                  { return nil }

func TestMissingFileMeansGone(t *testing.T) {
	hasher := Hasher{Open: memoryOpen(nil), Sleep: func(time.Duration) {}, Now: time.Now}

	if got := hasher.Hash(`E:\none`, 1<<20, time.Now().Add(time.Second)); got.Status != HashGone {
		t.Fatalf("результат: %+v", got)
	}
}

func TestLockedFileIsRetriedThenReportedUnavailable(t *testing.T) {
	attempts := 0
	now := t0
	hasher := Hasher{
		Open: func(string) (io.ReadCloser, int64, error) {
			attempts++
			return nil, 0, errors.New("процесс не может получить доступ к файлу")
		},
		Sleep: func(d time.Duration) { now = now.Add(d) },
		Now:   func() time.Time { return now },
	}

	result := hasher.Hash(`C:\locked.pst`, 1<<20, t0.Add(2*time.Second))

	if result.Status != HashUnavailable {
		t.Fatalf("результат: %+v", result)
	}
	if attempts < 3 || attempts > 40 {
		t.Fatalf("попыток %d: повторы должны быть, но не бесконечные", attempts)
	}
}

func TestLockedFileThatOpensLaterIsHashed(t *testing.T) {
	attempts := 0
	hasher := Hasher{
		Open: func(string) (io.ReadCloser, int64, error) {
			attempts++
			if attempts < 3 {
				return nil, 0, errors.New("занят")
			}
			return io.NopCloser(strings.NewReader("ok")), 2, nil
		},
		Sleep: func(time.Duration) {}, Now: func() time.Time { return t0 },
	}

	if got := hasher.Hash(`C:\x`, 1<<20, t0.Add(time.Hour)); got.Status != HashOK {
		t.Fatalf("результат: %+v", got)
	}
}

func TestDefaultHasherRejectsDirectories(t *testing.T) {
	dir := t.TempDir()
	file := filepath.Join(dir, "f.txt")
	os.WriteFile(file, []byte("data"), 0o600)
	hasher := NewHasher()

	if got := hasher.Hash(dir, 1<<20, time.Now().Add(time.Second)); got.Status != HashGone {
		t.Fatalf("каталог: %+v", got)
	}
	if got := hasher.Hash(file, 1<<20, time.Now().Add(time.Second)); got.Status != HashOK {
		t.Fatalf("файл: %+v", got)
	}
}
```

- [ ] **Step 5: Том по пути, конверт, разбор уведомлений — падающие тесты**

`volumeof_test.go`:

```go
package filewatch

import (
	"testing"

	"github.com/barysguard/agent/internal/volumes"
)

func TestVolumeForMatchesDriveLetterIgnoringCase(t *testing.T) {
	vols := []volumes.Volume{
		{DriveLetter: "C:", Type: volumes.TypeFixed},
		{DriveLetter: "E:", Type: volumes.TypeRemovable, Serial: "0781-5583"},
	}

	if got := VolumeFor(`e:\Отчёт 2026\a.xlsx`, vols); got.Serial != "0781-5583" {
		t.Fatalf("том: %+v", got)
	}
	if got := VolumeFor(`C:\Users\u\a.txt`, vols); got.Type != volumes.TypeFixed {
		t.Fatalf("том: %+v", got)
	}
	if got := VolumeFor(`Z:\none`, vols); got.Type != volumes.TypeUnknown {
		t.Fatalf("неизвестный том: %+v", got)
	}
	if got := VolumeFor(`\\server\share\a.txt`, vols); got.Type != volumes.TypeUnknown {
		t.Fatalf("UNC-путь: %+v", got)
	}
}
```

`event_test.go`:

```go
package filewatch

import (
	"testing"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

var removable = volumes.Volume{DriveLetter: "E:", Type: volumes.TypeRemovable, Serial: "0781-5583", Label: "KINGSTON", FS: "NTFS"}
var fixed = volumes.Volume{DriveLetter: "C:", Type: volumes.TypeFixed, Serial: "AAAA-BBBB", FS: "NTFS"}

func TestCopyToRemovableIsHighWithSourceAndArtifact(t *testing.T) {
	env, err := BuildEvent(EventInput{
		Action: ActionCopy, DstPath: `E:\отчёт.xlsx`, SrcPath: `C:\Users\u\Documents\отчёт.xlsx`, Volume: removable,
		Hash:    HashResult{SHA256: "a3f9", Size: 184320, Status: HashOK},
		Actor:   map[string]any{"user_name": "PC\\u"},
		Process: map[string]any{"pid": 4812, "path": `C:\Windows\explorer.exe`},
	})
	if err != nil {
		t.Fatal(err)
	}

	if env.Channel != "file" || env.Action != "copy" || env.SeverityHint != events.SeverityHigh {
		t.Fatalf("конверт: %+v", env)
	}
	if env.Subject["dst_path"] != `E:\отчёт.xlsx` || env.Subject["src_path"] != `C:\Users\u\Documents\отчёт.xlsx` {
		t.Fatalf("пути: %+v", env.Subject)
	}
	volume := env.Subject["volume"].(map[string]any)
	if volume["type"] != "removable" || volume["serial"] != "0781-5583" {
		t.Fatalf("том: %+v", volume)
	}
	if env.Artifact == nil || env.Artifact.SHA256 != "a3f9" || env.Artifact.Size != 184320 || env.Artifact.Uploaded {
		t.Fatalf("артефакт: %+v", env.Artifact)
	}
	if env.Process["pid"] != 4812 || env.Actor["user_name"] != "PC\\u" {
		t.Fatalf("актёр и процесс: %+v %+v", env.Actor, env.Process)
	}
	if _, has := env.Labels["process"]; has {
		t.Fatalf("процесс известен, метка не нужна: %+v", env.Labels)
	}
}

func TestSeverityByLocationAndAction(t *testing.T) {
	cases := []struct {
		action Action
		vol    volumes.Volume
		want   string
	}{
		{ActionCreate, removable, events.SeverityMedium},
		{ActionModify, removable, events.SeverityMedium},
		{ActionDelete, removable, events.SeverityInfo},
		{ActionRename, removable, events.SeverityInfo},
		{ActionCreate, fixed, events.SeverityInfo},
		{ActionModify, fixed, events.SeverityInfo},
	}
	for _, c := range cases {
		env, err := BuildEvent(EventInput{Action: c.action, DstPath: `X:\f`, Volume: c.vol, OldPath: `X:\o`})
		if err != nil {
			t.Fatal(err)
		}
		if env.SeverityHint != c.want {
			t.Errorf("%s на %s: %s, ожидалось %s", c.action, c.vol.Type, env.SeverityHint, c.want)
		}
	}
}

func TestUnknownProcessOnRemovableIsLabelled(t *testing.T) {
	env, _ := BuildEvent(EventInput{Action: ActionCreate, DstPath: `E:\a`, Volume: removable, Hash: HashResult{SHA256: "aa", Size: 1, Status: HashOK}})

	if env.Labels["process"] != "unknown" {
		t.Fatalf("метки: %+v", env.Labels)
	}
	del, _ := BuildEvent(EventInput{Action: ActionDelete, DstPath: `E:\a`, Volume: removable})
	if _, has := del.Labels["process"]; has {
		t.Fatalf("для удаления процесс не определяется: %+v", del.Labels)
	}
}

func TestMissingHashIsExplainedInLabelsAndOmitsArtifact(t *testing.T) {
	skipped, _ := BuildEvent(EventInput{Action: ActionCreate, DstPath: `E:\big`, Volume: removable,
		Hash: HashResult{Size: 5 << 30, Status: HashSkippedSize}})
	if skipped.Artifact != nil || skipped.Labels["hash"] != "skipped_size" || skipped.Subject["size_bytes"] != int64(5<<30) {
		t.Fatalf("skipped: %+v %+v", skipped.Labels, skipped.Subject)
	}

	unavailable, _ := BuildEvent(EventInput{Action: ActionModify, DstPath: `C:\locked`, Volume: fixed,
		Hash: HashResult{Status: HashUnavailable}})
	if unavailable.Artifact != nil || unavailable.Labels["hash"] != "unavailable" {
		t.Fatalf("unavailable: %+v", unavailable.Labels)
	}
}

func TestRenameCarriesOldPathAndDeleteOmitsArtifact(t *testing.T) {
	rename, _ := BuildEvent(EventInput{Action: ActionRename, DstPath: `E:\new`, OldPath: `E:\old`, Volume: removable,
		Hash: HashResult{SHA256: "aa", Size: 3, Status: HashOK}})
	if rename.Subject["old_path"] != `E:\old` {
		t.Fatalf("rename: %+v", rename.Subject)
	}
	del, _ := BuildEvent(EventInput{Action: ActionDelete, DstPath: `E:\gone`, Volume: removable})
	if del.Artifact != nil || del.Subject["dst_path"] != `E:\gone` {
		t.Fatalf("delete: %+v", del)
	}
	if _, has := del.Subject["old_path"]; has {
		t.Fatalf("old_path только для rename: %+v", del.Subject)
	}
}
```

`notify_test.go`:

```go
package filewatch

import (
	"encoding/binary"
	"reflect"
	"strings"
	"testing"
	"unicode/utf16"
)

type notification struct {
	action uint32
	name   string
}

// buildBuffer собирает FILE_NOTIFY_INFORMATION как отдаёт ReadDirectoryChangesW.
func buildBuffer(items ...notification) []byte {
	var buf []byte
	for i, item := range items {
		name := utf16.Encode([]rune(item.name))
		size := 12 + len(name)*2
		padded := (size + 3) &^ 3
		record := make([]byte, padded)
		next := uint32(padded)
		if i == len(items)-1 {
			next = 0
		}
		binary.LittleEndian.PutUint32(record[0:], next)
		binary.LittleEndian.PutUint32(record[4:], item.action)
		binary.LittleEndian.PutUint32(record[8:], uint32(len(name)*2))
		for j, unit := range name {
			binary.LittleEndian.PutUint16(record[12+j*2:], unit)
		}
		buf = append(buf, record...)
	}
	return buf
}

func TestParseSimpleActions(t *testing.T) {
	buf := buildBuffer(
		notification{1, `Отчёт 2026.xlsx`},
		notification{3, `Отчёт 2026.xlsx`},
		notification{2, `sub\old.txt`},
	)

	got := ParseNotifications(`E:\`, buf)

	want := []Raw{
		{Kind: Created, Path: `E:\Отчёт 2026.xlsx`},
		{Kind: Modified, Path: `E:\Отчёт 2026.xlsx`},
		{Kind: Deleted, Path: `E:\sub\old.txt`},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("разбор: %+v", got)
	}
}

func TestParsePairsRenames(t *testing.T) {
	buf := buildBuffer(notification{4, "old.txt"}, notification{5, "new.txt"})

	got := ParseNotifications(`C:\Users\u\Documents`, buf)

	want := []Raw{{Kind: Renamed, Path: `C:\Users\u\Documents\new.txt`, OldPath: `C:\Users\u\Documents\old.txt`}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("разбор: %+v", got)
	}
}

func TestUnpairedRenameHalvesDegradeToDeleteAndCreate(t *testing.T) {
	// Файл переименован из наблюдаемой папки наружу или обратно.
	got := ParseNotifications(`C:\d`, buildBuffer(notification{4, "gone.txt"}))
	if len(got) != 1 || got[0].Kind != Deleted || got[0].Path != `C:\d\gone.txt` {
		t.Fatalf("OLD без NEW: %+v", got)
	}
	got = ParseNotifications(`C:\d`, buildBuffer(notification{5, "came.txt"}))
	if len(got) != 1 || got[0].Kind != Created || got[0].Path != `C:\d\came.txt` {
		t.Fatalf("NEW без OLD: %+v", got)
	}
}

func TestLongNamesAreParsedWithoutTruncation(t *testing.T) {
	name := strings.Repeat("вложенная-папка-", 25) + `файл.txt` // длиннее 260 символов
	got := ParseNotifications(`C:\d`, buildBuffer(notification{1, name}))

	if len(got) != 1 || got[0].Path != `C:\d\`+name {
		t.Fatalf("длинное имя: %d записей", len(got))
	}
}

func TestMalformedBuffersDoNotPanic(t *testing.T) {
	valid := buildBuffer(notification{1, "a.txt"})
	truncated := valid[:10]
	oversized := append([]byte(nil), valid...)
	binary.LittleEndian.PutUint32(oversized[8:], 9999)
	badNext := append([]byte(nil), valid...)
	binary.LittleEndian.PutUint32(badNext[0:], 1<<30)

	for _, data := range [][]byte{nil, {1, 2, 3}, truncated, oversized, badNext} {
		ParseNotifications(`C:\d`, data)
	}
}

func TestRootWithTrailingBackslashIsNotDoubled(t *testing.T) {
	got := ParseNotifications(`E:\`, buildBuffer(notification{1, "a.txt"}))
	if got[0].Path != `E:\a.txt` {
		t.Fatalf("путь: %q", got[0].Path)
	}
}
```

- [ ] **Step 6: Конвейер — падающие тесты `pipeline_test.go`**

```go
package filewatch

import (
	"io"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

type fakeIdentity struct {
	owner, console map[string]any
}

func (f fakeIdentity) FileOwner(string) map[string]any { return f.owner }
func (f fakeIdentity) ConsoleUser() map[string]any     { return f.console }

type fakeAttributor struct{ process map[string]any }

func (f fakeAttributor) Attribute(string) (map[string]any, bool) { return f.process, f.process != nil }

type harness struct {
	pipeline *Pipeline
	emitted  []events.Envelope
	now      time.Time
	files    map[string]string
}

func newHarness(t *testing.T, mutate func(*PipelineDeps, *Config)) *harness {
	t.Helper()
	h := &harness{now: t0, files: map[string]string{}}
	cfg := Config{Enabled: true, Stable: 1500 * time.Millisecond, MaxWait: 30 * time.Second, MaxHashBytes: 1 << 20, MaxEventsPerSecond: 1000}
	deps := PipelineDeps{
		Volumes: func() []volumes.Volume {
			return []volumes.Volume{
				{DriveLetter: "C:", Type: volumes.TypeFixed, Serial: "AAAA-BBBB"},
				{DriveLetter: "E:", Type: volumes.TypeRemovable, Serial: "0781-5583", Label: "K"},
			}
		},
		Hasher: Hasher{
			Open: func(path string) (io.ReadCloser, int64, error) {
				content, ok := h.files[path]
				if !ok {
					return nil, 0, os.ErrNotExist
				}
				return io.NopCloser(strings.NewReader(content)), int64(len(content)), nil
			},
			Sleep: func(time.Duration) {}, Now: func() time.Time { return h.now },
		},
		Identity: fakeIdentity{
			owner:   map[string]any{"user_sid": "S-1-5-21-1-2-3-1001", "user_name": "PC\\ivanov"},
			console: map[string]any{"user_sid": "S-1-5-21-1-2-3-1002", "user_name": "PC\\console"},
		},
		Emit: func(e events.Envelope) { h.emitted = append(h.emitted, e) },
		Now:  func() time.Time { return h.now },
	}
	if mutate != nil {
		mutate(&deps, &cfg)
	}
	deps.Config = cfg
	h.pipeline = NewPipeline(deps)
	return h
}

func (h *harness) advance(d time.Duration) { h.now = h.now.Add(d); h.pipeline.Tick() }

const src = `C:\Users\ivanov\Documents\отчёт.xlsx`
const dst = `E:\отчёт.xlsx`

func TestFileCopiedToAFlashDriveIsReportedAsCopyWithItsSource(t *testing.T) {
	h := newHarness(t, nil)
	h.files[src] = "содержимое отчёта"
	h.files[dst] = "содержимое отчёта"

	// Агент видит файл в наблюдаемой папке…
	h.pipeline.Handle(Raw{Kind: Modified, Path: src})
	h.advance(2 * time.Second)
	// …потом он появляется на флешке.
	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if len(h.emitted) != 2 {
		t.Fatalf("событий %d: %+v", len(h.emitted), h.emitted)
	}
	if h.emitted[0].Action != "modify" || h.emitted[0].SeverityHint != events.SeverityInfo {
		t.Fatalf("первое: %+v", h.emitted[0])
	}
	copyEvent := h.emitted[1]
	if copyEvent.Action != "copy" || copyEvent.SeverityHint != events.SeverityHigh || copyEvent.Subject["src_path"] != src {
		t.Fatalf("копирование: %+v", copyEvent)
	}
	if copyEvent.Artifact == nil || copyEvent.Actor["user_name"] != "PC\\ivanov" {
		t.Fatalf("артефакт и актёр: %+v %+v", copyEvent.Artifact, copyEvent.Actor)
	}
}

func TestUnseenFileOnAFlashDriveIsAnOrdinaryCreate(t *testing.T) {
	h := newHarness(t, nil)
	h.files[dst] = "что-то, чего агент не видел"

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if len(h.emitted) != 1 || h.emitted[0].Action != "create" || h.emitted[0].SeverityHint != events.SeverityMedium {
		t.Fatalf("события: %+v", h.emitted)
	}
	if _, has := h.emitted[0].Subject["src_path"]; has {
		t.Fatalf("src_path без источника: %+v", h.emitted[0].Subject)
	}
}

func TestExcludedPathsAreIgnored(t *testing.T) {
	h := newHarness(t, func(_ *PipelineDeps, cfg *Config) { cfg.Exclude = []string{`*\~$*`, `*.tmp`} })
	h.files[`C:\d\~$a.docx`] = "x"

	h.pipeline.Handle(Raw{Kind: Created, Path: `C:\d\~$a.docx`})
	h.advance(5 * time.Second)

	if len(h.emitted) != 0 {
		t.Fatalf("исключённый файл дал событие: %+v", h.emitted)
	}
}

func TestSaveThroughATempFileIsOneCreateEvent(t *testing.T) {
	h := newHarness(t, func(_ *PipelineDeps, cfg *Config) { cfg.Exclude = []string{`*.tmp`} })
	h.files[`C:\d\report.docx`] = "новая версия"

	h.pipeline.Handle(Raw{Kind: Renamed, OldPath: `C:\d\~WRD0001.tmp`, Path: `C:\d\report.docx`})
	h.advance(2 * time.Second)

	if len(h.emitted) != 1 || h.emitted[0].Action != "create" {
		t.Fatalf("события: %+v", h.emitted)
	}
}

func TestVanishedFileProducesNoEvent(t *testing.T) {
	h := newHarness(t, nil) // файла в h.files нет: Open отдаёт ошибку несуществования
	h.pipeline.deps.Hasher.Open = func(string) (io.ReadCloser, int64, error) { return nil, 0, errNotExist }

	h.pipeline.Handle(Raw{Kind: Created, Path: `C:\d\temp.dat`})
	h.advance(2 * time.Second)

	if len(h.emitted) != 0 {
		t.Fatalf("исчезнувший файл дал событие: %+v", h.emitted)
	}
}

func TestDeleteNeedsNoHash(t *testing.T) {
	h := newHarness(t, nil)

	h.pipeline.Handle(Raw{Kind: Deleted, Path: dst})
	h.advance(2 * time.Second)

	if len(h.emitted) != 1 || h.emitted[0].Action != "delete" || h.emitted[0].Artifact != nil {
		t.Fatalf("события: %+v", h.emitted)
	}
}

func TestProcessIsAttributedOnlyOnRemovableVolumes(t *testing.T) {
	h := newHarness(t, func(d *PipelineDeps, _ *Config) {
		d.Attributor = fakeAttributor{process: map[string]any{"pid": 77, "path": `C:\Windows\explorer.exe`}}
	})
	h.files[dst] = "a"
	h.files[src] = "b"

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.pipeline.Handle(Raw{Kind: Created, Path: src})
	h.advance(2 * time.Second)

	byPath := map[string]events.Envelope{}
	for _, e := range h.emitted {
		byPath[e.Subject["dst_path"].(string)] = e
	}
	if byPath[dst].Process["pid"] != 77 {
		t.Fatalf("на флешке процесс не определён: %+v", byPath[dst])
	}
	if byPath[src].Process != nil {
		t.Fatalf("в наблюдаемой папке процесс не запрашивается: %+v", byPath[src])
	}
}

func TestServiceOwnerFallsBackToTheConsoleUser(t *testing.T) {
	h := newHarness(t, func(d *PipelineDeps, _ *Config) {
		d.Identity = fakeIdentity{
			owner:   map[string]any{"user_sid": "S-1-5-32-544", "user_name": `BUILTIN\Administrators`},
			console: map[string]any{"user_sid": "S-1-5-21-1-2-3-1002", "user_name": "PC\\console"},
		}
	})
	h.files[dst] = "x"

	h.pipeline.Handle(Raw{Kind: Created, Path: dst})
	h.advance(2 * time.Second)

	if h.emitted[0].Actor["user_name"] != "PC\\console" {
		t.Fatalf("актёр: %+v", h.emitted[0].Actor)
	}
}

// Распаковка архива: тысячи файлов за секунду не должны ни остановить сборщик,
// ни пройти все разом; потеря обязана быть сообщена.
func TestEventStormIsThrottledAndTheLossIsReported(t *testing.T) {
	h := newHarness(t, func(_ *PipelineDeps, cfg *Config) { cfg.MaxEventsPerSecond = 10 })
	for i := 0; i < 500; i++ {
		path := `C:\Users\ivanov\Downloads\arch\f` + strings.Repeat("x", i%7) + string(rune('a'+i%26)) + string(rune('A'+i/26%26)) + string(rune('0'+i/676)) + `.txt`
		h.files[path] = "данные"
		h.pipeline.Handle(Raw{Kind: Created, Path: path})
	}
	h.advance(2 * time.Second)

	files, dropped := 0, 0
	for _, e := range h.emitted {
		switch {
		case e.Channel == "file":
			files++
		case e.Channel == "agent" && e.Action == "events_dropped":
			dropped += int(e.Subject["count"].(uint64))
		}
	}
	if files > 12 || files < 1 {
		t.Fatalf("прошло %d событий при лимите 10 в секунду", files)
	}
	if files+dropped < 400 {
		t.Fatalf("потери не сосчитаны: прошло %d, потеряно %d из ~500", files, dropped)
	}
}
```

Тестовый файл использует `errNotExist`; определите в `pipeline_test.go` рядом с импортами: `var errNotExist = os.ErrNotExist`. Заморозка часов в харнессе (`Now` возвращает `h.now`) требует, чтобы отсутствующий файл сообщался как `os.ErrNotExist`: любая другая ошибка запускала бы цикл повторов хешера, который при замороженных часах не кончился бы.

- [ ] **Step 7: Запустить, убедиться, что всё падает**

Run: `cd agent && go test ./internal/collectors/filewatch/... 2>&1 | head -12`
Expected: FAIL (ничего не определено).

- [ ] **Step 8: `config.go` и `exclude.go`**

`agent/internal/collectors/filewatch/config.go`:

```go
// Package filewatch — сборщик канала file: наблюдение за наблюдаемыми папками
// и внешними томами, склейка уведомлений, хеширование, сопоставление копирования.
package filewatch

import (
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Умолчания совпадают с серверными (services/config.py); агент обязан работать
// и с документом, в котором раздела collectors нет.
const (
	defaultStableMs     = 1500
	defaultMaxWaitMs    = 30_000
	defaultMaxHashBytes = 256 * 1024 * 1024
	defaultMaxPerSecond = 200
)

var (
	defaultPaths   = []string{`%USERS%\Documents`, `%USERS%\Desktop`, `%USERS%\Downloads`}
	defaultExclude = []string{`*\~$*`, `*.tmp`, `*.crdownload`, `*\AppData\*`}
)

type Config struct {
	Enabled            bool
	Paths              []string
	Exclude            []string
	Stable, MaxWait    time.Duration
	MaxHashBytes       int64
	MaxEventsPerSecond int
}

func section(document map[string]any, names ...string) map[string]any {
	current := document
	for _, name := range names {
		next, _ := current[name].(map[string]any)
		if next == nil {
			return nil
		}
		current = next
	}
	return current
}

func number(section map[string]any, key string, fallback float64) float64 {
	if value, ok := section[key].(float64); ok && value > 0 {
		return value
	}
	return fallback
}

func stringList(section map[string]any, key string, fallback []string) []string {
	raw, ok := section[key].([]any)
	if !ok {
		return append([]string(nil), fallback...)
	}
	var out []string
	for _, item := range raw {
		if text, ok := item.(string); ok && text != "" {
			out = append(out, text)
		}
	}
	return out
}

// ConfigFromDocument читает collectors.file_watch. Отсутствующее или
// бессмысленное значение заменяется умолчанием: плохой документ не должен
// оставлять агента без наблюдения. profiles — каталоги профилей для %USERS%,
// dataDir — рабочий каталог агента, который никогда не наблюдается.
func ConfigFromDocument(document map[string]any, profiles []string, dataDir string) Config {
	fw := section(document, "collectors", "file_watch")

	cfg := Config{Enabled: true}
	if enabled, ok := fw["enabled"].(bool); ok {
		cfg.Enabled = enabled
	}
	cfg.Stable = time.Duration(number(fw, "stable_ms", defaultStableMs)) * time.Millisecond
	cfg.MaxWait = time.Duration(number(fw, "max_wait_ms", defaultMaxWaitMs)) * time.Millisecond
	cfg.MaxHashBytes = int64(number(fw, "max_hash_bytes", defaultMaxHashBytes))
	cfg.MaxEventsPerSecond = int(number(fw, "max_events_per_second", defaultMaxPerSecond))
	cfg.Paths = expandPaths(stringList(fw, "paths", defaultPaths), profiles)
	cfg.Exclude = stringList(fw, "exclude", defaultExclude)
	if dataDir != "" {
		cfg.Exclude = append(cfg.Exclude, strings.TrimRight(dataDir, `\/`)+`\*`)
	}
	return cfg
}

func expandPaths(paths, profiles []string) []string {
	const token = "%USERS%"
	var out []string
	for _, path := range paths {
		if !strings.Contains(path, token) {
			out = append(out, path)
			continue
		}
		for _, profile := range profiles {
			out = append(out, strings.Replace(path, token, strings.TrimRight(profile, `\/`), 1))
		}
	}
	return out
}

// ProfileDirs перечисляет профили пользователей в usersRoot, кроме системных.
func ProfileDirs(usersRoot string) []string {
	skip := map[string]bool{"public": true, "default": true, "default user": true, "all users": true}
	entries, err := os.ReadDir(usersRoot)
	if err != nil {
		return nil
	}
	var out []string
	for _, entry := range entries {
		if !entry.IsDir() || skip[strings.ToLower(entry.Name())] {
			continue
		}
		out = append(out, filepath.Join(usersRoot, entry.Name()))
	}
	return out
}
```

`agent/internal/collectors/filewatch/exclude.go`:

```go
package filewatch

import "strings"

// Excluder сопоставляет путь со списком шаблонов. Шаблон — простая маска:
// * совпадает с любой последовательностью символов (включая разделители),
// ? с одним символом; регистр не учитывается, как в файловой системе Windows.
type Excluder struct{ patterns []string }

func NewExcluder(patterns []string) *Excluder {
	lowered := make([]string, 0, len(patterns))
	for _, pattern := range patterns {
		lowered = append(lowered, strings.ToLower(pattern))
	}
	return &Excluder{patterns: lowered}
}

func (e *Excluder) Match(path string) bool {
	lowered := strings.ToLower(path)
	for _, pattern := range e.patterns {
		if globMatch([]rune(pattern), []rune(lowered)) {
			return true
		}
	}
	return false
}

// globMatch — итеративное сопоставление с откатом к последней звёздочке,
// без рекурсии: путь длиной в тысячи символов не должен переполнить стек.
func globMatch(pattern, text []rune) bool {
	p, t := 0, 0
	star, mark := -1, 0
	for t < len(text) {
		switch {
		case p < len(pattern) && (pattern[p] == '?' || pattern[p] == text[t]):
			p++
			t++
		case p < len(pattern) && pattern[p] == '*':
			star, mark = p, t
			p++
		case star >= 0:
			p = star + 1
			mark++
			t = mark
		default:
			return false
		}
	}
	for p < len(pattern) && pattern[p] == '*' {
		p++
	}
	return p == len(pattern)
}
```

- [ ] **Step 9: `debounce.go`, `hashindex.go`, `limiter.go`**

`agent/internal/collectors/filewatch/debounce.go`:

```go
package filewatch

import (
	"sort"
	"strings"
	"time"
)

type Op int

const (
	OpCreate Op = iota
	OpModify
	OpDelete
)

type Action string

const (
	ActionCreate Action = "create"
	ActionModify Action = "modify"
	ActionDelete Action = "delete"
	ActionRename Action = "rename"
	ActionCopy   Action = "copy"
)

// Settled — итог склейки: один файл, одно действие.
type Settled struct {
	Path, OldPath string
	Action        Action
}

type pending struct {
	path        string // последнее написание: регистр в путях значим только для показа
	first, last time.Time
	created     bool
	deleted     bool
	lastDelete  bool
	renamedFrom string
}

// Debouncer склеивает поток уведомлений в события по одному на файл.
// Одна копия файла порождает десятки уведомлений; событие выходит, когда
// файл стабилен. Не потокобезопасен: им владеет цикл сборщика.
type Debouncer struct{ items map[string]*pending }

func NewDebouncer() *Debouncer { return &Debouncer{items: map[string]*pending{}} }

func key(path string) string { return strings.ToLower(path) }

func (d *Debouncer) touch(path string, now time.Time) *pending {
	item, ok := d.items[key(path)]
	if !ok {
		item = &pending{first: now}
		d.items[key(path)] = item
	}
	item.path = path
	item.last = now
	return item
}

func (d *Debouncer) Notify(path string, op Op, now time.Time) {
	item := d.touch(path, now)
	switch op {
	case OpCreate:
		item.created, item.lastDelete = true, false
	case OpModify:
		item.lastDelete = false
	case OpDelete:
		item.deleted, item.lastDelete = true, true
	}
}

// NotifyRename учитывает переименование. Если старое имя появилось в том же
// окне склейки (редактор писал временный файл и переименовал его), новое имя
// считается созданием: настоящего «старого файла» никто не видел.
func (d *Debouncer) NotifyRename(oldPath, newPath string, now time.Time) {
	createdHere := false
	if old, ok := d.items[key(oldPath)]; ok {
		createdHere = old.created
		delete(d.items, key(oldPath))
	}
	item := d.touch(newPath, now)
	item.lastDelete = false
	if createdHere {
		item.created = true
		return
	}
	item.renamedFrom = oldPath
}

// Settle отдаёт события, по которым нет новых уведомлений дольше stable
// либо которые ждут дольше maxWait, и забывает их.
func (d *Debouncer) Settle(now time.Time, stable, maxWait time.Duration) []Settled {
	type ready struct {
		item *pending
		out  Settled
	}
	var done []ready
	for k, item := range d.items {
		if now.Sub(item.last) < stable && now.Sub(item.first) < maxWait {
			continue
		}
		delete(d.items, k)

		out := Settled{Path: item.path}
		switch {
		case item.lastDelete && item.created:
			continue // создан и удалён в одном окне: временный файл
		case item.lastDelete:
			out.Action = ActionDelete
		case item.deleted && item.created:
			out.Action = ActionModify // файл заменён: редактор удалил и создал заново
		case item.renamedFrom != "":
			out.Action, out.OldPath = ActionRename, item.renamedFrom
		case item.created:
			out.Action = ActionCreate
		default:
			out.Action = ActionModify
		}
		done = append(done, ready{item: item, out: out})
	}

	sort.Slice(done, func(i, j int) bool {
		if !done[i].item.first.Equal(done[j].item.first) {
			return done[i].item.first.Before(done[j].item.first)
		}
		return done[i].out.Path < done[j].out.Path
	})
	result := make([]Settled, 0, len(done))
	for _, entry := range done {
		result = append(result, entry.out)
	}
	return result
}
```

`agent/internal/collectors/filewatch/hashindex.go`:

```go
package filewatch

import (
	"container/list"
	"time"
)

type indexKey struct {
	sha  string
	size int64
}

type indexEntry struct {
	key  indexKey
	path string
	seen time.Time
}

// HashIndex помнит, где агент недавно видел файл с данным содержимым.
// Ограничен по числу записей и по сроку: он нужен только чтобы узнать
// источник копирования, а не как архив.
type HashIndex struct {
	max int
	ttl time.Duration
	ll  *list.List
	m   map[indexKey]*list.Element
}

func NewHashIndex(max int, ttl time.Duration) *HashIndex {
	return &HashIndex{max: max, ttl: ttl, ll: list.New(), m: map[indexKey]*list.Element{}}
}

func (h *HashIndex) Put(sha string, size int64, path string, now time.Time) {
	k := indexKey{sha, size}
	if element, ok := h.m[k]; ok {
		entry := element.Value.(*indexEntry)
		entry.path, entry.seen = path, now
		h.ll.MoveToFront(element)
		return
	}
	h.m[k] = h.ll.PushFront(&indexEntry{key: k, path: path, seen: now})
	for h.ll.Len() > h.max {
		oldest := h.ll.Back()
		delete(h.m, oldest.Value.(*indexEntry).key)
		h.ll.Remove(oldest)
	}
}

func (h *HashIndex) Lookup(sha string, size int64, now time.Time) (string, bool) {
	element, ok := h.m[indexKey{sha, size}]
	if !ok {
		return "", false
	}
	entry := element.Value.(*indexEntry)
	if now.Sub(entry.seen) > h.ttl {
		delete(h.m, entry.key)
		h.ll.Remove(element)
		return "", false
	}
	h.ll.MoveToFront(element)
	return entry.path, true
}
```

`agent/internal/collectors/filewatch/limiter.go`:

```go
package filewatch

import "time"

// Limiter — окно в одну секунду: массовые изменения (распаковка архива)
// не должны ни остановить сборщик, ни залить очередь событий.
type Limiter struct {
	perSecond   int
	windowStart time.Time
	used        int
}

func NewLimiter(perSecond int) *Limiter { return &Limiter{perSecond: perSecond} }

func (l *Limiter) Allow(now time.Time) bool {
	if now.Sub(l.windowStart) >= time.Second {
		l.windowStart, l.used = now, 0
	}
	if l.used >= l.perSecond {
		return false
	}
	l.used++
	return true
}
```

- [ ] **Step 10: `hasher.go`, `volumeof.go`, `event.go`**

`agent/internal/collectors/filewatch/hasher.go`:

```go
package filewatch

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"time"
)

const (
	HashOK          = "ok"
	HashSkippedSize = "skipped_size"
	HashUnavailable = "unavailable"
	// HashGone — файла уже нет или это каталог: события не будет.
	HashGone = "gone"
)

var errIsDirectory = errors.New("это каталог")

type HashResult struct {
	SHA256 string
	Size   int64
	Status string
}

// Hasher считает SHA-256 файла потоком. Зависимости подменяются в тестах.
type Hasher struct {
	Open  func(path string) (io.ReadCloser, int64, error)
	Sleep func(time.Duration)
	Now   func() time.Time
}

func NewHasher() Hasher {
	return Hasher{Open: openFile, Sleep: time.Sleep, Now: time.Now}
}

func openFile(path string) (io.ReadCloser, int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	info, err := file.Stat()
	if err != nil {
		file.Close()
		return nil, 0, err
	}
	if info.IsDir() {
		file.Close()
		return nil, 0, errIsDirectory
	}
	return file, info.Size(), nil
}

// Hash открывает файл и считает хеш. Файл, который копируют прямо сейчас, часто
// занят: попытки повторяются с нарастающей паузой до deadline, затем событие
// уходит без хеша, а не откладывается навсегда. Файл больше maxBytes не читается.
func (h Hasher) Hash(path string, maxBytes int64, deadline time.Time) HashResult {
	delay := 50 * time.Millisecond
	for {
		reader, size, err := h.Open(path)
		if err == nil {
			result, readErr := hashReader(reader, size, maxBytes)
			reader.Close()
			if readErr == nil {
				return result
			}
			err = readErr
		}
		if errors.Is(err, os.ErrNotExist) || errors.Is(err, errIsDirectory) {
			return HashResult{Status: HashGone}
		}
		if !h.Now().Add(delay).Before(deadline) {
			return HashResult{Status: HashUnavailable}
		}
		h.Sleep(delay)
		if delay < time.Second {
			delay *= 2
		}
	}
}

func hashReader(reader io.Reader, size, maxBytes int64) (HashResult, error) {
	if size > maxBytes {
		return HashResult{Size: size, Status: HashSkippedSize}, nil
	}
	sum := sha256.New()
	read, err := io.Copy(sum, reader)
	if err != nil {
		return HashResult{}, err
	}
	return HashResult{SHA256: hex.EncodeToString(sum.Sum(nil)), Size: read, Status: HashOK}, nil
}
```

`agent/internal/collectors/filewatch/volumeof.go`:

```go
package filewatch

import (
	"strings"

	"github.com/barysguard/agent/internal/volumes"
)

// VolumeFor находит том по букве диска в начале пути. Сетевые пути (\\server\share)
// и неизвестные буквы дают том неизвестного типа: событие не теряется,
// просто без серийного номера.
func VolumeFor(path string, vols []volumes.Volume) volumes.Volume {
	if len(path) >= 2 && path[1] == ':' {
		letter := path[:2]
		for _, volume := range vols {
			if strings.EqualFold(volume.DriveLetter, letter) {
				return volume
			}
		}
	}
	return volumes.Volume{Type: volumes.TypeUnknown}
}
```

`agent/internal/collectors/filewatch/event.go`:

```go
package filewatch

import (
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

type EventInput struct {
	Action                    Action
	DstPath, SrcPath, OldPath string
	Volume                    volumes.Volume
	Hash                      HashResult
	Actor, Process            map[string]any
}

// severityFor: подсказка агента. Копирование известного файла на внешний том —
// главный сценарий утечки; запись неизвестного — повод присмотреться.
func severityFor(action Action, vol volumes.Volume) string {
	if vol.Type != volumes.TypeRemovable {
		return events.SeverityInfo
	}
	switch action {
	case ActionCopy:
		return events.SeverityHigh
	case ActionCreate, ActionModify:
		return events.SeverityMedium
	}
	return events.SeverityInfo
}

// BuildEvent собирает конверт канала file (раздел 7.1 спеки).
func BuildEvent(in EventInput) (events.Envelope, error) {
	subject := map[string]any{
		"dst_path": in.DstPath,
		"volume": map[string]any{
			"type": in.Volume.Type, "serial": in.Volume.Serial, "label": in.Volume.Label, "fs": in.Volume.FS,
		},
	}
	if in.Action == ActionCopy && in.SrcPath != "" {
		subject["src_path"] = in.SrcPath
	}
	if in.Action == ActionRename && in.OldPath != "" {
		subject["old_path"] = in.OldPath
	}

	env, err := events.NewEnvelope(events.ChannelFile, string(in.Action), severityFor(in.Action, in.Volume), subject)
	if err != nil {
		return events.Envelope{}, err
	}

	labels := map[string]any{}
	switch in.Hash.Status {
	case HashOK:
		env.Artifact = &events.Artifact{SHA256: in.Hash.SHA256, Size: in.Hash.Size, Uploaded: false}
		subject["size_bytes"] = in.Hash.Size
	case HashSkippedSize:
		labels["hash"] = "skipped_size"
		subject["size_bytes"] = in.Hash.Size
	case HashUnavailable:
		labels["hash"] = "unavailable"
	}
	if in.Volume.Type == volumes.TypeRemovable && in.Action != ActionDelete && in.Process == nil {
		labels["process"] = "unknown"
	}

	env.Actor = in.Actor
	env.Process = in.Process
	env.Labels = labels
	return env, nil
}
```

- [ ] **Step 11: `notify.go` и `pipeline.go`**

`agent/internal/collectors/filewatch/notify.go`:

```go
package filewatch

import (
	"encoding/binary"
	"strings"
	"unicode/utf16"
)

type Kind int

const (
	Created Kind = iota
	Modified
	Deleted
	Renamed
)

// Raw — одно разобранное уведомление файловой системы.
type Raw struct {
	Kind          Kind
	Path, OldPath string
}

const (
	actionAdded      = 1
	actionRemoved    = 2
	actionModified   = 3
	actionRenamedOld = 4
	actionRenamedNew = 5
	// Заголовок FILE_NOTIFY_INFORMATION: NextEntryOffset, Action, FileNameLength.
	notifyHeader = 12
)

func join(root, name string) string {
	return strings.TrimRight(root, `\`) + `\` + name
}

// ParseNotifications разбирает буфер ReadDirectoryChangesW. Вынесено из
// Windows-кода: разбор проверяется на любой платформе и не должен паниковать
// на повреждённом буфере. Пара RENAMED_OLD_NAME/RENAMED_NEW_NAME собирается
// в одно переименование; половина пары превращается в удаление или создание
// (файл переименован из наблюдаемой папки наружу или обратно).
func ParseNotifications(root string, buf []byte) []Raw {
	var out []Raw
	pendingOld := ""
	flushOld := func() {
		if pendingOld != "" {
			out = append(out, Raw{Kind: Deleted, Path: pendingOld})
			pendingOld = ""
		}
	}

	offset := 0
	for offset+notifyHeader <= len(buf) {
		next := int(binary.LittleEndian.Uint32(buf[offset:]))
		action := binary.LittleEndian.Uint32(buf[offset+4:])
		nameLen := int(binary.LittleEndian.Uint32(buf[offset+8:]))
		nameStart := offset + notifyHeader
		if nameLen%2 != 0 || nameStart+nameLen > len(buf) {
			break
		}

		units := make([]uint16, nameLen/2)
		for i := range units {
			units[i] = binary.LittleEndian.Uint16(buf[nameStart+i*2:])
		}
		path := join(root, string(utf16.Decode(units)))

		if action != actionRenamedNew {
			flushOld()
		}
		switch action {
		case actionAdded:
			out = append(out, Raw{Kind: Created, Path: path})
		case actionRemoved:
			out = append(out, Raw{Kind: Deleted, Path: path})
		case actionModified:
			out = append(out, Raw{Kind: Modified, Path: path})
		case actionRenamedOld:
			pendingOld = path
		case actionRenamedNew:
			if pendingOld != "" {
				out = append(out, Raw{Kind: Renamed, Path: path, OldPath: pendingOld})
				pendingOld = ""
			} else {
				out = append(out, Raw{Kind: Created, Path: path})
			}
		}

		if next == 0 {
			break
		}
		offset += next
	}
	flushOld()
	return out
}
```

`agent/internal/collectors/filewatch/pipeline.go`:

```go
package filewatch

import (
	"log/slog"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/identity"
	"github.com/barysguard/agent/internal/volumes"
)

// Attributor определяет процесс, работавший с файлом. Реализация на Restart
// Manager — лучшая из доступных без драйвера; ETW подключается позже за тем же
// интерфейсом.
type Attributor interface {
	Attribute(path string) (map[string]any, bool)
}

const (
	indexCapacity = 10_000
	indexTTL      = 24 * time.Hour
)

type PipelineDeps struct {
	Config     Config
	Volumes    func() []volumes.Volume
	Hasher     Hasher
	Identity   identity.Resolver
	Attributor Attributor
	Emit       func(events.Envelope)
	Now        func() time.Time
}

// Pipeline превращает поток уведомлений в события. Не потокобезопасен:
// Handle и Tick вызываются из одного цикла.
type Pipeline struct {
	deps    PipelineDeps
	deb     *Debouncer
	idx     *HashIndex
	ex      *Excluder
	lim     *Limiter
	dropped uint64
}

func NewPipeline(deps PipelineDeps) *Pipeline {
	return &Pipeline{
		deps: deps,
		deb:  NewDebouncer(),
		idx:  NewHashIndex(indexCapacity, indexTTL),
		ex:   NewExcluder(deps.Config.Exclude),
		lim:  NewLimiter(deps.Config.MaxEventsPerSecond),
	}
}

func (p *Pipeline) Handle(raw Raw) {
	now := p.deps.Now()
	switch raw.Kind {
	case Created:
		if !p.ex.Match(raw.Path) {
			p.deb.Notify(raw.Path, OpCreate, now)
		}
	case Modified:
		if !p.ex.Match(raw.Path) {
			p.deb.Notify(raw.Path, OpModify, now)
		}
	case Deleted:
		if !p.ex.Match(raw.Path) {
			p.deb.Notify(raw.Path, OpDelete, now)
		}
	case Renamed:
		switch {
		case p.ex.Match(raw.Path):
			// Файл стал временным: прежнее имя исчезло.
			if !p.ex.Match(raw.OldPath) {
				p.deb.Notify(raw.OldPath, OpDelete, now)
			}
		case p.ex.Match(raw.OldPath):
			// Временный файл превратился в настоящий (так сохраняют редакторы):
			// для оператора это появление нового файла.
			p.deb.Notify(raw.Path, OpCreate, now)
		default:
			p.deb.NotifyRename(raw.OldPath, raw.Path, now)
		}
	}
}

// Tick выпускает событие по каждому установившемуся файлу и, если что-то
// отброшено ограничителем, сообщает об этом отдельным событием.
func (p *Pipeline) Tick() {
	now := p.deps.Now()
	cfg := p.deps.Config
	for _, settled := range p.deb.Settle(now, cfg.Stable, cfg.MaxWait) {
		p.emit(settled, now)
	}
	if p.dropped > 0 {
		report, err := events.NewEnvelope(events.ChannelAgent, "events_dropped", events.SeverityInfo, map[string]any{
			"component": "filewatch",
			"detail":    "слишком много файловых событий в секунду, часть отброшена",
			"count":     p.dropped,
		})
		if err == nil {
			p.deps.Emit(report)
		}
		p.dropped = 0
	}
}

func (p *Pipeline) emit(settled Settled, now time.Time) {
	// Ограничитель стоит до хеширования: цель — не нагружать диск, а не
	// только очередь.
	if !p.lim.Allow(now) {
		p.dropped++
		return
	}

	cfg := p.deps.Config
	vol := VolumeFor(settled.Path, p.deps.Volumes())
	removable := vol.Type == volumes.TypeRemovable

	in := EventInput{Action: settled.Action, DstPath: settled.Path, OldPath: settled.OldPath, Volume: vol}
	if settled.Action != ActionDelete {
		in.Hash = p.deps.Hasher.Hash(settled.Path, cfg.MaxHashBytes, now.Add(cfg.MaxWait))
		if in.Hash.Status == HashGone {
			return // временный файл или каталог
		}
	}

	if in.Hash.Status == HashOK && (settled.Action == ActionCreate || settled.Action == ActionModify) {
		if removable {
			if source, ok := p.idx.Lookup(in.Hash.SHA256, in.Hash.Size, now); ok && source != settled.Path {
				in.Action, in.SrcPath = ActionCopy, source
			}
		} else {
			p.idx.Put(in.Hash.SHA256, in.Hash.Size, settled.Path, now)
		}
	}

	if p.deps.Identity != nil {
		in.Actor = identity.Pick(p.deps.Identity.FileOwner(settled.Path), p.deps.Identity.ConsoleUser())
	}
	// Процесс определяется только для событий на внешних томах: Restart Manager
	// дорог, а интересны именно они.
	if removable && settled.Action != ActionDelete && p.deps.Attributor != nil {
		if process, ok := p.deps.Attributor.Attribute(settled.Path); ok {
			in.Process = process
		}
	}

	env, err := BuildEvent(in)
	if err != nil {
		slog.Error("событие файла не создано", "path", settled.Path, "error", err)
		return
	}
	p.deps.Emit(env)
}
```

- [ ] **Step 12: Запустить тесты пакета**

```bash
cd agent && gofmt -l internal/collectors; go vet ./internal/collectors/... && go test ./internal/collectors/filewatch/... -count=1 -race 2>&1 | tail -20
```

Expected: PASS. Если `TestEventStormIsThrottled...` падает на именах файлов (повторы путей склеиваются в одно событие), поправьте генерацию путей в тесте так, чтобы все 500 путей были различны (например, `fmt.Sprintf(`C:\Users\ivanov\Downloads\arch\f%d.txt`, i)`) — тест проверяет ограничитель, а не склейку.

- [ ] **Step 13: Линтеры и коммит**

```bash
cd agent && go vet ./... && GOOS=linux go build ./... && echo linux-ok
git add agent/internal/collectors/filewatch
git commit -m "feat(agent): filewatch core — config, debounce, hasher, hash index, copy matching, pipeline" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `filewatch` — наблюдатель Windows, Restart Manager, сборщик

**Files:**
- Create: `agent/internal/collectors/filewatch/watcher_windows.go`, `watcher_other.go`, `attrib_windows.go`, `attrib_other.go`, `collector.go`
- Test: `agent/internal/collectors/filewatch/watcher_windows_test.go`, `attrib_windows_test.go`, `collector_test.go`

**Interfaces:**
- Consumes: всё из задачи 5, `volumes.Hub`, `identity.Resolver`.
- Produces: `type StartWatcher func(ctx context.Context, root string, out chan<- Raw, overflow func(root string)) error`; `DefaultStartWatcher StartWatcher` (Windows — `ReadDirectoryChangesW`; прочие — всегда `ErrUnsupported`); `NewAttributor() Attributor`; `ErrUnsupported`; `type Deps struct{Config Config; Hub *volumes.Hub; Identity identity.Resolver; Attributor Attributor; StartWatcher StartWatcher; Now func() time.Time}`; `New(Deps) *Collector` (`events.Collector`).

- [ ] **Step 1: Падающий тест сборщика `collector_test.go` (переносимый, на заглушках)**

```go
package filewatch

import (
	"context"
	"errors"
	"io"
	"io/fs"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/volumes"
)

type fixedProvider struct {
	mu   sync.Mutex
	vols []volumes.Volume
}

func (f *fixedProvider) Snapshot() ([]volumes.Volume, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]volumes.Volume(nil), f.vols...), nil
}

func (f *fixedProvider) set(v []volumes.Volume) { f.mu.Lock(); f.vols = v; f.mu.Unlock() }

// watchLog запоминает, какие корни просили наблюдать, и даёт сценарию
// подсунуть уведомления.
type watchLog struct {
	mu      sync.Mutex
	started map[string]chan<- Raw
	stopped map[string]bool
	denied  map[string]error
}

func newWatchLog() *watchLog {
	return &watchLog{started: map[string]chan<- Raw{}, stopped: map[string]bool{}, denied: map[string]error{}}
}

func (w *watchLog) start(ctx context.Context, root string, out chan<- Raw, _ func(string)) error {
	w.mu.Lock()
	if err, ok := w.denied[root]; ok {
		w.mu.Unlock()
		return err
	}
	w.started[root] = out
	w.mu.Unlock()
	<-ctx.Done()
	w.mu.Lock()
	w.stopped[root] = true
	w.mu.Unlock()
	return nil
}

func (w *watchLog) out(root string) (chan<- Raw, bool) {
	w.mu.Lock()
	defer w.mu.Unlock()
	c, ok := w.started[root]
	return c, ok
}

func (w *watchLog) wasStopped(root string) bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.stopped[root]
}

func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("не дождались: %s", what)
}

type collected struct {
	mu   sync.Mutex
	list []events.Envelope
}

func (c *collected) add(e events.Envelope) { c.mu.Lock(); c.list = append(c.list, e); c.mu.Unlock() }
func (c *collected) snapshot() []events.Envelope {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]events.Envelope(nil), c.list...)
}

func startCollector(t *testing.T, cfg Config, provider *fixedProvider, log *watchLog, files map[string]string) (*collected, func()) {
	t.Helper()
	hub := volumes.NewHub(provider, 20*time.Millisecond)
	hasher := Hasher{
		Open: func(path string) (io.ReadCloser, int64, error) {
			content, ok := files[path]
			if !ok {
				return nil, 0, fs.ErrNotExist
			}
			return io.NopCloser(strings.NewReader(content)), int64(len(content)), nil
		},
		Sleep: func(time.Duration) {}, Now: time.Now,
	}
	collector := New(Deps{Config: cfg, Hub: hub, Hasher: hasher, StartWatcher: log.start, Now: time.Now, RootExists: func(string) bool { return true }})

	got := &collected{}
	ctx, cancel := context.WithCancel(context.Background())
	hubDone := make(chan struct{})
	done := make(chan struct{})
	go func() { hub.Run(ctx, nil); close(hubDone) }()
	go func() { collector.Run(ctx, got.add); close(done) }()
	return got, func() { cancel(); <-done; <-hubDone }
}

var quick = Config{Enabled: true, Stable: 30 * time.Millisecond, MaxWait: time.Second, MaxHashBytes: 1 << 20, MaxEventsPerSecond: 1000}

func TestCollectorWatchesConfiguredPathsAndEmitsFileEvents(t *testing.T) {
	log := newWatchLog()
	provider := &fixedProvider{vols: []volumes.Volume{{DriveLetter: "C:", Type: volumes.TypeFixed, Serial: "AAAA"}}}
	cfg := quick
	cfg.Paths = []string{`C:\Users\u\Documents`}
	got, stop := startCollector(t, cfg, provider, log, map[string]string{`C:\Users\u\Documents\a.txt`: "данные"})
	defer stop()

	eventually(t, "наблюдатель папки запущен", func() bool { _, ok := log.out(`C:\Users\u\Documents`); return ok })
	out, _ := log.out(`C:\Users\u\Documents`)
	out <- Raw{Kind: Created, Path: `C:\Users\u\Documents\a.txt`}

	eventually(t, "событие file/create", func() bool {
		for _, e := range got.snapshot() {
			if e.Channel == "file" && e.Action == "create" {
				return true
			}
		}
		return false
	})
}

func TestRemovableVolumeRootIsWatchedWhileItIsMounted(t *testing.T) {
	log := newWatchLog()
	provider := &fixedProvider{}
	cfg := quick
	cfg.Paths = nil
	_, stop := startCollector(t, cfg, provider, log, nil)
	defer stop()

	provider.set([]volumes.Volume{{DriveLetter: "E:", Type: volumes.TypeRemovable, Bus: volumes.BusUSB, Serial: "0781"}})
	eventually(t, "корень флешки наблюдается", func() bool { _, ok := log.out(`E:\`); return ok })

	// Извлечение флешки останавливает наблюдателя: дескриптор не должен утечь.
	provider.set(nil)
	eventually(t, "наблюдатель флешки остановлен", func() bool { return log.wasStopped(`E:\`) })
}

func TestFixedVolumesAreNotWatchedAsRemovable(t *testing.T) {
	log := newWatchLog()
	provider := &fixedProvider{vols: []volumes.Volume{{DriveLetter: "D:", Type: volumes.TypeFixed, Serial: "DDDD"}}}
	cfg := quick
	cfg.Paths = nil
	_, stop := startCollector(t, cfg, provider, log, nil)
	defer stop()

	time.Sleep(150 * time.Millisecond)

	if _, ok := log.out(`D:\`); ok {
		t.Fatal("фиксированный том не должен наблюдаться целиком")
	}
}

// Нет доступа к папке другого профиля: сообщаем и продолжаем с остальными.
func TestDeniedRootIsReportedAndOthersKeepWorking(t *testing.T) {
	log := newWatchLog()
	log.denied[`C:\Users\other\Documents`] = fs.ErrPermission
	provider := &fixedProvider{vols: []volumes.Volume{{DriveLetter: "C:", Type: volumes.TypeFixed, Serial: "AAAA"}}}
	cfg := quick
	cfg.Paths = []string{`C:\Users\other\Documents`, `C:\Users\me\Documents`}
	got, stop := startCollector(t, cfg, provider, log, nil)
	defer stop()

	eventually(t, "своя папка наблюдается", func() bool { _, ok := log.out(`C:\Users\me\Documents`); return ok })
	eventually(t, "agent/watch_denied по чужой папке", func() bool {
		for _, e := range got.snapshot() {
			if e.Channel == "agent" && e.Action == "watch_denied" && strings.Contains(e.Subject["detail"].(string), `other`) {
				return true
			}
		}
		return false
	})
}

func TestMissingRootsAreSilentlySkipped(t *testing.T) {
	log := newWatchLog()
	log.denied[`C:\Users\u\Desktop`] = fs.ErrNotExist
	provider := &fixedProvider{}
	cfg := quick
	cfg.Paths = []string{`C:\Users\u\Desktop`}
	got, stop := startCollector(t, cfg, provider, log, nil)
	defer stop()

	time.Sleep(150 * time.Millisecond)

	for _, e := range got.snapshot() {
		if e.Action == "watch_denied" {
			t.Fatalf("отсутствующая папка не повод для события: %+v", e)
		}
	}
	if !errors.Is(fs.ErrNotExist, fs.ErrNotExist) {
		t.Fatal("sanity")
	}
}

func TestCollectorStopsOnCancelAndStopsItsWatchers(t *testing.T) {
	log := newWatchLog()
	provider := &fixedProvider{}
	cfg := quick
	cfg.Paths = []string{`C:\Users\u\Documents`}
	_, stop := startCollector(t, cfg, provider, log, nil)

	eventually(t, "наблюдатель запущен", func() bool { _, ok := log.out(`C:\Users\u\Documents`); return ok })
	stop()

	if !log.wasStopped(`C:\Users\u\Documents`) {
		t.Fatal("наблюдатель не остановлен вместе со сборщиком")
	}
}
```

Тест использует `Deps.Hasher` и `Deps.RootExists`: добавьте их в `Deps` (ниже). `RootExists func(string) bool` нужен, чтобы на Linux-CI не обращаться к диску: в боевой сборке по умолчанию `os.Stat`.

- [ ] **Step 2: Падающие Windows-тесты**

`agent/internal/collectors/filewatch/watcher_windows_test.go`:

```go
//go:build windows

package filewatch

import (
	"context"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func startRoot(t *testing.T, root string) (<-chan Raw, context.CancelFunc, <-chan error) {
	t.Helper()
	out := make(chan Raw, 256)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- DefaultStartWatcher(ctx, root, out, func(string) {}) }()
	time.Sleep(200 * time.Millisecond) // дать ReadDirectoryChanges встать на ожидание
	return out, cancel, done
}

func waitRaw(t *testing.T, out <-chan Raw, match func(Raw) bool) Raw {
	t.Helper()
	timeout := time.After(5 * time.Second)
	for {
		select {
		case raw := <-out:
			if match(raw) {
				return raw
			}
		case <-timeout:
			t.Fatal("ожидаемое уведомление не пришло")
		}
	}
}

func TestWatcherSeesCreateModifyRenameAndDelete(t *testing.T) {
	root := t.TempDir()
	out, cancel, done := startRoot(t, root)
	defer func() { cancel(); <-done }()

	name := filepath.Join(root, "Отчёт 2026.txt")
	if err := os.WriteFile(name, []byte("первая версия"), 0o600); err != nil {
		t.Fatal(err)
	}
	waitRaw(t, out, func(r Raw) bool { return r.Kind == Created && r.Path == name })

	renamed := filepath.Join(root, "Отчёт — финал.txt")
	if err := os.Rename(name, renamed); err != nil {
		t.Fatal(err)
	}
	got := waitRaw(t, out, func(r Raw) bool { return r.Kind == Renamed })
	if got.OldPath != name || got.Path != renamed {
		t.Fatalf("переименование: %+v", got)
	}

	if err := os.Remove(renamed); err != nil {
		t.Fatal(err)
	}
	waitRaw(t, out, func(r Raw) bool { return r.Kind == Deleted && r.Path == renamed })
}

func TestWatcherIsRecursive(t *testing.T) {
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, "вложенная", "глубже"), 0o755)
	out, cancel, done := startRoot(t, root)
	defer func() { cancel(); <-done }()

	deep := filepath.Join(root, "вложенная", "глубже", "f.txt")
	os.WriteFile(deep, []byte("x"), 0o600)

	waitRaw(t, out, func(r Raw) bool { return r.Kind == Created && r.Path == deep })
}

func TestWatcherStopsPromptlyOnCancel(t *testing.T) {
	_, cancel, done := startRoot(t, t.TempDir())

	cancel()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("остановка вернула ошибку: %v", err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("наблюдатель завис после отмены")
	}
}

func TestMissingRootIsReportedAsNotExist(t *testing.T) {
	err := DefaultStartWatcher(context.Background(), filepath.Join(t.TempDir(), "нет"), make(chan Raw, 1), func(string) {})

	if !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("ошибка: %v", err)
	}
}
```

`agent/internal/collectors/filewatch/attrib_windows_test.go`:

```go
//go:build windows

package filewatch

import (
	"os"
	"path/filepath"
	"testing"
)

// Restart Manager видит процесс, удерживающий файл открытым. Пока копирование
// идёт, этим процессом бывает проводник или robocopy; в тесте им служим мы сами.
func TestAttributorFindsTheProcessHoldingTheFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "held.bin")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()

	attributor := &rmAttributor{self: 0} // свой PID обычно исключается; в тесте именно мы держим файл
	process, ok := attributor.Attribute(path)

	if !ok {
		t.Skip("Restart Manager не вернул процесс на этой системе")
	}
	if process["pid"] != os.Getpid() {
		t.Fatalf("процесс: %+v, ожидался наш PID %d", process, os.Getpid())
	}
	if p, _ := process["path"].(string); p == "" {
		t.Fatalf("нет пути образа: %+v", process)
	}
}

func TestAttributorExcludesTheAgentItself(t *testing.T) {
	path := filepath.Join(t.TempDir(), "held.bin")
	file, _ := os.OpenFile(path, os.O_CREATE|os.O_WRONLY, 0o600)
	defer file.Close()

	if process, ok := NewAttributor().Attribute(path); ok && process["pid"] == os.Getpid() {
		t.Fatalf("агент определил самого себя: %+v", process)
	}
}

func TestAttributorOnAFreeOrMissingFileFindsNobody(t *testing.T) {
	free := filepath.Join(t.TempDir(), "free.bin")
	os.WriteFile(free, []byte("x"), 0o600)

	if _, ok := NewAttributor().Attribute(free); ok {
		t.Fatal("процесс найден для свободного файла")
	}
	if _, ok := NewAttributor().Attribute(filepath.Join(t.TempDir(), "нет.bin")); ok {
		t.Fatal("процесс найден для несуществующего файла")
	}
}
```

- [ ] **Step 3: Запустить, убедиться, что падает**

Run: `cd agent && go test ./internal/collectors/filewatch/... 2>&1 | head -8`
Expected: FAIL (`undefined: New`, `DefaultStartWatcher`, `NewAttributor`, `rmAttributor`).

- [ ] **Step 4: Заглушки `watcher_other.go`, `attrib_other.go`**

```go
//go:build !windows

package filewatch

import (
	"context"
	"errors"
)

// ErrUnsupported — наблюдение за файлами не реализовано на этой платформе.
var ErrUnsupported = errors.New("наблюдение за файлами не поддерживается на этой платформе")

// StartWatcher наблюдает за корнем root, пока не отменён ctx, и передаёт
// разобранные уведомления в out; overflow сообщает о переполнении буфера ОС.
type StartWatcher func(ctx context.Context, root string, out chan<- Raw, overflow func(root string)) error

var DefaultStartWatcher StartWatcher = func(context.Context, string, chan<- Raw, func(string)) error {
	return ErrUnsupported
}
```

```go
//go:build !windows

package filewatch

type noAttributor struct{}

func (noAttributor) Attribute(string) (map[string]any, bool) { return nil, false }

func NewAttributor() Attributor { return noAttributor{} }
```

- [ ] **Step 5: `watcher_windows.go`**

```go
//go:build windows

package filewatch

import (
	"context"
	"errors"
	"time"

	"golang.org/x/sys/windows"
)

// ErrUnsupported совпадает по смыслу с заглушкой других платформ; на Windows
// не возвращается, но нужен для единообразного кода сборщика.
var ErrUnsupported = errors.New("наблюдение за файлами не поддерживается на этой платформе")

// StartWatcher наблюдает за корнем root, пока не отменён ctx, и передаёт
// разобранные уведомления в out; overflow сообщает о переполнении буфера ОС.
type StartWatcher func(ctx context.Context, root string, out chan<- Raw, overflow func(root string)) error

const (
	watchBufferSize = 64 * 1024
	watchMask       = windows.FILE_NOTIFY_CHANGE_FILE_NAME | windows.FILE_NOTIFY_CHANGE_DIR_NAME |
		windows.FILE_NOTIFY_CHANGE_SIZE | windows.FILE_NOTIFY_CHANGE_LAST_WRITE
)

// DefaultStartWatcher — блокирующий цикл ReadDirectoryChangesW на корень.
// Каждый корень получает собственную горутину: цикл заблокирован в системном
// вызове, остановить его можно только отменой ввода-вывода.
var DefaultStartWatcher StartWatcher = func(ctx context.Context, root string, out chan<- Raw, overflow func(string)) error {
	path, err := windows.UTF16PtrFromString(root)
	if err != nil {
		return err
	}
	handle, err := windows.CreateFile(path, windows.FILE_LIST_DIRECTORY,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE|windows.FILE_SHARE_DELETE,
		nil, windows.OPEN_EXISTING, windows.FILE_FLAG_BACKUP_SEMANTICS, 0)
	if err != nil {
		return err
	}

	done := make(chan struct{})
	defer func() {
		close(done)
		windows.CloseHandle(handle)
	}()
	// Отмена, пришедшая раньше, чем вызов встал на ожидание, ни во что бы не
	// упёрлась и осталась бы незамеченной: поэтому отмена повторяется, пока
	// цикл не завершится.
	go func() {
		select {
		case <-ctx.Done():
		case <-done:
			return
		}
		for {
			windows.CancelIoEx(handle, nil)
			select {
			case <-done:
				return
			case <-time.After(50 * time.Millisecond):
			}
		}
	}()

	buffer := make([]byte, watchBufferSize)
	for {
		if ctx.Err() != nil {
			return nil
		}
		var returned uint32
		err := windows.ReadDirectoryChanges(handle, &buffer[0], uint32(len(buffer)), true, watchMask, &returned, nil, 0)
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		if returned == 0 {
			// Буфер переполнился: система выбросила изменения. Сообщаем и
			// продолжаем; повторного сканирования аудит не требует.
			overflow(root)
			continue
		}
		for _, raw := range ParseNotifications(root, buffer[:returned]) {
			select {
			case out <- raw:
			case <-ctx.Done():
				return nil
			}
		}
	}
}
```

- [ ] **Step 6: `attrib_windows.go` (Restart Manager)**

```go
//go:build windows

package filewatch

import (
	"os"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	rstrtmgr       = windows.NewLazySystemDLL("rstrtmgr.dll")
	procRmStart    = rstrtmgr.NewProc("RmStartSession")
	procRmRegister = rstrtmgr.NewProc("RmRegisterResources")
	procRmGetList  = rstrtmgr.NewProc("RmGetList")
	procRmEnd      = rstrtmgr.NewProc("RmEndSession")
)

const (
	cchRmSessionKey = 32
	errorMoreData   = 234
	maxHolders      = 16
)

type rmUniqueProcess struct {
	ProcessID uint32
	StartTime windows.Filetime
}

type rmProcessInfo struct {
	Process          rmUniqueProcess
	AppName          [256]uint16
	ServiceShortName [64]uint16
	ApplicationType  uint32
	AppStatus        uint32
	TSSessionID      uint32
	Restartable      int32
}

// rmAttributor определяет процесс через Restart Manager: он перечисляет
// процессы, удерживающие файл открытым. Пока идёт копирование, это копирующий
// процесс; после закрытия дескриптора ответ пуст — тогда событие честно
// помечается «процесс не определён». self — PID самого агента: он открывает
// файл для хеширования и не должен определять сам себя.
type rmAttributor struct{ self int }

func NewAttributor() Attributor { return &rmAttributor{self: os.Getpid()} }

func (a *rmAttributor) Attribute(path string) (map[string]any, bool) {
	var session uint32
	key := make([]uint16, cchRmSessionKey+1)
	if result, _, _ := procRmStart.Call(uintptr(unsafe.Pointer(&session)), 0, uintptr(unsafe.Pointer(&key[0]))); result != 0 {
		return nil, false
	}
	defer procRmEnd.Call(uintptr(session))

	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return nil, false
	}
	if result, _, _ := procRmRegister.Call(uintptr(session), 1, uintptr(unsafe.Pointer(&name)), 0, 0, 0, 0); result != 0 {
		return nil, false
	}

	infos := make([]rmProcessInfo, maxHolders)
	var needed, count, reasons uint32
	count = uint32(len(infos))
	result, _, _ := procRmGetList.Call(uintptr(session), uintptr(unsafe.Pointer(&needed)),
		uintptr(unsafe.Pointer(&count)), uintptr(unsafe.Pointer(&infos[0])), uintptr(unsafe.Pointer(&reasons)))
	if result == errorMoreData && needed > 0 {
		infos = make([]rmProcessInfo, needed)
		count = needed
		result, _, _ = procRmGetList.Call(uintptr(session), uintptr(unsafe.Pointer(&needed)),
			uintptr(unsafe.Pointer(&count)), uintptr(unsafe.Pointer(&infos[0])), uintptr(unsafe.Pointer(&reasons)))
	}
	if result != 0 {
		return nil, false
	}

	for _, info := range infos[:count] {
		pid := int(info.Process.ProcessID)
		if pid == 0 || pid == 4 || pid == a.self {
			continue
		}
		return map[string]any{"pid": pid, "path": imagePath(uint32(pid), windows.UTF16ToString(info.AppName[:]))}, true
	}
	return nil, false
}

// imagePath возвращает полный путь образа процесса; при отказе в доступе
// (процесс повышенный) остаётся имя приложения от Restart Manager.
func imagePath(pid uint32, fallback string) string {
	handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return fallback
	}
	defer windows.CloseHandle(handle)
	buffer := make([]uint16, windows.MAX_LONG_PATH)
	size := uint32(len(buffer))
	if err := windows.QueryFullProcessImageName(handle, 0, &buffer[0], &size); err != nil {
		return fallback
	}
	return windows.UTF16ToString(buffer[:size])
}
```

- [ ] **Step 7: `collector.go`**

```go
package filewatch

import (
	"context"
	"errors"
	"io/fs"
	"log/slog"
	"os"
	"sync"
	"time"

	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/identity"
	"github.com/barysguard/agent/internal/volumes"
)

const (
	tickInterval = 250 * time.Millisecond
	rawBuffer    = 4096
)

type Deps struct {
	Config       Config
	Hub          *volumes.Hub
	Identity     identity.Resolver
	Attributor   Attributor
	StartWatcher StartWatcher
	// Hasher и RootExists подменяются в тестах; нулевые значения дают боевые.
	Hasher     Hasher
	RootExists func(root string) bool
	Now        func() time.Time
}

type Collector struct{ deps Deps }

func New(deps Deps) *Collector {
	if deps.Now == nil {
		deps.Now = time.Now
	}
	if deps.Hasher.Open == nil {
		deps.Hasher = NewHasher()
	}
	if deps.StartWatcher == nil {
		deps.StartWatcher = DefaultStartWatcher
	}
	if deps.RootExists == nil {
		deps.RootExists = func(root string) bool { _, err := os.Stat(root); return err == nil }
	}
	return &Collector{deps: deps}
}

func (c *Collector) Name() string { return "filewatch" }

type root struct {
	cancel context.CancelFunc
	done   chan struct{}
}

// Run наблюдает за папками из конфигурации и за корнями внешних томов,
// пока те подключены. Всё — в одном цикле: уведомления, подключения томов
// и тик склейки обрабатываются последовательно, без общих блокировок.
func (c *Collector) Run(ctx context.Context, emit func(events.Envelope)) error {
	cfg := c.deps.Config
	raw := make(chan Raw, rawBuffer)
	pipeline := NewPipeline(PipelineDeps{
		Config:     cfg,
		Volumes:    func() []volumes.Volume { return c.deps.Hub.Current() },
		Hasher:     c.deps.Hasher,
		Identity:   c.deps.Identity,
		Attributor: c.deps.Attributor,
		Emit:       emit,
		Now:        c.deps.Now,
	})

	roots := map[string]*root{}
	report := func(action, path, detail string) {
		env, err := events.NewEnvelope(events.ChannelAgent, action, events.SeverityLow, map[string]any{
			"component": "filewatch",
			"detail":    detail + ": " + path,
		})
		if err == nil {
			emit(env)
		}
	}
	start := func(path string) {
		if _, running := roots[path]; running {
			return
		}
		if !c.deps.RootExists(path) {
			return // у профиля может не быть Desktop: это не событие
		}
		rootCtx, cancel := context.WithCancel(ctx)
		entry := &root{cancel: cancel, done: make(chan struct{})}
		roots[path] = entry
		go func() {
			defer close(entry.done)
			err := c.deps.StartWatcher(rootCtx, path, raw, func(overflowed string) {
				report("watch_overflow", overflowed, "буфер уведомлений переполнен, изменения потеряны")
			})
			switch {
			case err == nil, rootCtx.Err() != nil:
			case errors.Is(err, fs.ErrNotExist):
				slog.Debug("наблюдаемая папка исчезла", "path", path)
			case errors.Is(err, fs.ErrPermission):
				report("watch_denied", path, "нет доступа к наблюдаемой папке")
			default:
				slog.Warn("наблюдение за папкой остановилось", "path", path, "error", err)
				report("watch_denied", path, "наблюдение остановилось: "+err.Error())
			}
		}()
	}
	stop := func(path string) {
		if entry, ok := roots[path]; ok {
			entry.cancel()
			<-entry.done
			delete(roots, path)
		}
	}
	stopAll := func() {
		for path := range roots {
			stop(path)
		}
	}

	for _, path := range cfg.Paths {
		start(path)
	}

	changes, unsubscribe := c.deps.Hub.Subscribe()
	defer unsubscribe()
	ticker := time.NewTicker(tickInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			stopAll()
			return nil
		case item := <-raw:
			pipeline.Handle(item)
		case change, ok := <-changes:
			if !ok {
				stopAll()
				return nil
			}
			if change.Volume.Type != volumes.TypeRemovable {
				continue
			}
			rootPath := change.Volume.DriveLetter + `\`
			if change.Mounted {
				start(rootPath)
			} else {
				stop(rootPath)
			}
		case <-ticker.C:
			pipeline.Tick()
		}
	}
}
```

Замечание: при остановке наблюдателя после `ctx.Done` горутина `StartWatcher` может быть заблокирована в отправке в `raw`; поскольку цикл `Run` в это время ждёт `<-entry.done`, отправка не продолжается — `StartWatcher` обязан возвращаться по отмене своего контекста (реализация Windows так и делает: отправка в `out` идёт через `select` с `ctx.Done()`).

- [ ] **Step 8: Запустить тесты**

```bash
cd agent && gofmt -l internal/collectors; go vet ./internal/collectors/... && go test ./internal/collectors/filewatch/... -count=1 -race -timeout 120s 2>&1 | tail -20
GOOS=linux go build ./... && echo linux-ok
```

Expected: PASS. Windows-тесты наблюдателя и Restart Manager выполняются на этой машине; допустим `SKIP` одного теста `TestAttributorFindsTheProcessHoldingTheFile` — он явно помечен, остальные обязаны проходить.

- [ ] **Step 9: Коммит**

```bash
git add agent/internal/collectors/filewatch
git commit -m "feat(agent): filewatch collector with ReadDirectoryChangesW watchers and Restart Manager attribution" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Фабрика сборщиков, перезапуск группы и подключение в агенте

**Files:**
- Create: `agent/internal/collectors/factory.go`
- Modify: `agent/internal/runner/events.go`, `agent/internal/runner/agent.go`, `agent/cmd/barysguard-agent/main.go`
- Test: `agent/internal/collectors/factory_test.go`; дополнить `agent/internal/runner/events_test.go`

**Interfaces:**
- Consumes: `volumes`, `usb`, `filewatch`, `identity` (задачи 2–6).
- Produces: `collectors.Platform{Provider volumes.Provider; Identity identity.Resolver; Attributor filewatch.Attributor; StartWatcher filewatch.StartWatcher; Profiles func() []string}`; `collectors.DefaultPlatform() Platform`; `collectors.Build(doc map[string]any, dataDir string, plat Platform) []events.Collector`; `collectors.NewFactory(dataDir string) func(map[string]any) []events.Collector`; `runner.Options.CollectorFactory func(doc map[string]any) []events.Collector`.

- [ ] **Step 1: Падающие тесты фабрики `agent/internal/collectors/factory_test.go`**

```go
package collectors

import (
	"context"
	"testing"

	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/identity"
	"github.com/barysguard/agent/internal/volumes"
)

type noVolumes struct{}

func (noVolumes) Snapshot() ([]volumes.Volume, error) { return nil, nil }

func fakePlatform() Platform {
	return Platform{
		Provider:     noVolumes{},
		Identity:     identity.New(),
		StartWatcher: func(context.Context, string, chan<- filewatch.Raw, func(string)) error { return nil },
		Profiles:     func() []string { return []string{`C:\Users\u`} },
	}
}

func names(list []events.Collector) []string {
	var out []string
	for _, c := range list {
		out = append(out, c.Name())
	}
	return out
}

func equal(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func TestDefaultDocumentStartsHubUSBAndFilewatch(t *testing.T) {
	got := names(Build(nil, `C:\data`, fakePlatform()))

	if !equal(got, []string{"volumes", "usb", "filewatch"}) {
		t.Fatalf("сборщики: %v", got)
	}
}

func TestDisabledChannelsAreLeftOut(t *testing.T) {
	onlyFiles := map[string]any{"collectors": map[string]any{"usb": map[string]any{"enabled": false}}}
	if got := names(Build(onlyFiles, "", fakePlatform())); !equal(got, []string{"volumes", "filewatch"}) {
		t.Fatalf("без usb: %v", got)
	}

	onlyUSB := map[string]any{"collectors": map[string]any{"file_watch": map[string]any{"enabled": false}}}
	if got := names(Build(onlyUSB, "", fakePlatform())); !equal(got, []string{"volumes", "usb"}) {
		t.Fatalf("без файлов: %v", got)
	}

	none := map[string]any{"collectors": map[string]any{
		"usb": map[string]any{"enabled": false}, "file_watch": map[string]any{"enabled": false},
	}}
	// Опрос томов нужен только сборщикам; без них он не запускается.
	if got := Build(none, "", fakePlatform()); len(got) != 0 {
		t.Fatalf("всё выключено, а сборщики есть: %v", names(got))
	}
}

func TestNonWindowsFactoryBuildsNothing(t *testing.T) {
	plat := fakePlatform()
	plat.Supported = false

	if got := NewFactoryFor(plat, "")(nil); len(got) != 0 {
		t.Fatalf("на неподдерживаемой платформе сборщики: %v", names(got))
	}
}
```

Фабрика тестируется с флагом `Supported` в `Platform`: на Windows `DefaultPlatform().Supported == true`.

- [ ] **Step 2: Падающие тесты перезапуска группы — дополнить `agent/internal/runner/events_test.go`**

Добавить в `fakeGateway` поле `configDocument map[string]any` и использовать его в ветке `/gateway/v1/config` (`Document: g.configDocument`, если не nil, иначе `map[string]any{}`). Импорты теста: `sync`, `sync/atomic`, `reflect` при необходимости. В конец файла:

```go
// probeCollector считает запуски и останавливается по отмене своего контекста.
type probeCollector struct {
	starts, stops *atomic.Int32
}

func (p probeCollector) Name() string { return "probe" }
func (p probeCollector) Run(ctx context.Context, _ func(events.Envelope)) error {
	p.starts.Add(1)
	<-ctx.Done()
	p.stops.Add(1)
	return nil
}

type factoryProbe struct {
	calls         atomic.Int32
	starts, stops atomic.Int32
	mu            sync.Mutex
	docs          []map[string]any
}

func (f *factoryProbe) build(doc map[string]any) []events.Collector {
	f.calls.Add(1)
	f.mu.Lock()
	f.docs = append(f.docs, doc)
	f.mu.Unlock()
	return []events.Collector{probeCollector{starts: &f.starts, stops: &f.stops}}
}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("не дождались: %s", what)
}

func newFactoryAgent(t *testing.T, gateway *fakeGateway, state config.State, probe *factoryProbe) *runner.Agent {
	t.Helper()
	gateway.ids = map[string]int{}
	ca := newRunnerCA(t)
	server := newRunnerTLSServer(t, ca, gateway)
	layout := config.NewLayout(t.TempDir())
	guard := platform.New()
	if err := config.SaveState(layout, state, guard); err != nil {
		t.Fatal(err)
	}
	client, err := transport.NewMutual(server.URL, ca.pool(), ca.issue(t, "agent", ""))
	if err != nil {
		t.Fatal(err)
	}
	buf, err := buffer.Open(buffer.Options{
		Path: filepath.Join(t.TempDir(), "events.db"), Key: bytes.Repeat([]byte{7}, 32),
		MaxBytes: 1 << 30, MaxAge: 24 * time.Hour,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { buf.Close() })

	agent, err := runner.New(runner.Options{
		ServerURL: server.URL, AgentVersion: "0.1.0", Layout: layout, Guard: guard, Client: client,
		Random: rand.NewSource(1), Buffer: buf, CollectorFactory: probe.build,
	})
	if err != nil {
		t.Fatal(err)
	}
	return agent
}

func TestFactoryCollectorsStartWithTheSavedDocument(t *testing.T) {
	probe := &factoryProbe{}
	state := config.State{ConfigVersion: 5, Document: map[string]any{"collectors": map[string]any{"usb": map[string]any{"enabled": false}}}}
	agent := newFactoryAgent(t, &fakeGateway{configVersion: 5}, state, probe)

	stop := agent.StartEvents(context.Background())
	waitFor(t, "сборщик запущен", func() bool { return probe.starts.Load() == 1 })
	stop()

	if probe.stops.Load() != 1 {
		t.Fatal("сборщик не остановлен вместе с агентом")
	}
	if usb, _ := probe.docs[0]["collectors"].(map[string]any)["usb"].(map[string]any); usb["enabled"] != false {
		t.Fatalf("фабрика получила не сохранённый документ: %+v", probe.docs[0])
	}
}

// Смена версии без смены раздела collectors не должна перезапускать сборщики:
// повторный старт опроса томов продублировал бы usb/mount для вставленных флешек.
func TestConfigChangeOutsideCollectorsDoesNotRestartThem(t *testing.T) {
	probe := &factoryProbe{}
	gateway := &fakeGateway{configVersion: 99, configDocument: map[string]any{"logging": map[string]any{"level": "debug"}}}
	agent := newFactoryAgent(t, gateway, config.State{ConfigVersion: 1}, probe)

	stop := agent.StartEvents(context.Background())
	waitFor(t, "первый запуск", func() bool { return probe.starts.Load() == 1 })
	agent.RunOnce(context.Background()) // версия 99 ≠ 1: документ сменился, но collectors нет
	time.Sleep(200 * time.Millisecond)
	stop()

	if probe.calls.Load() != 1 || probe.starts.Load() != 1 {
		t.Fatalf("фабрика вызвана %d раз, запусков %d: перезапуска быть не должно", probe.calls.Load(), probe.starts.Load())
	}
}

func TestChangedCollectorsSectionRestartsTheGroupWithTheNewDocument(t *testing.T) {
	probe := &factoryProbe{}
	newCollectors := map[string]any{"usb": map[string]any{"enabled": false}}
	gateway := &fakeGateway{configVersion: 99, configDocument: map[string]any{"collectors": newCollectors}}
	agent := newFactoryAgent(t, gateway, config.State{ConfigVersion: 1}, probe)

	stop := agent.StartEvents(context.Background())
	waitFor(t, "первый запуск", func() bool { return probe.starts.Load() == 1 })
	agent.RunOnce(context.Background())
	waitFor(t, "перезапуск группы", func() bool { return probe.starts.Load() == 2 })
	stop()

	if probe.stops.Load() != 2 {
		t.Fatalf("остановок %d: старая группа обязана остановиться до новой", probe.stops.Load())
	}
	probe.mu.Lock()
	defer probe.mu.Unlock()
	if got := probe.docs[1]["collectors"]; !reflect.DeepEqual(got, newCollectors) {
		t.Fatalf("новая группа собрана по старому документу: %+v", got)
	}
}
```

Добавьте в импорты теста `reflect`.

- [ ] **Step 3: Запустить, убедиться, что падает**

Run: `cd agent && go test ./internal/collectors/ ./internal/runner/... 2>&1 | head -10`
Expected: FAIL (`collectors.Platform`, `Options.CollectorFactory` не определены).

- [ ] **Step 4: `agent/internal/collectors/factory.go`**

```go
// Package collectors собирает набор сборщиков событий из документа конфигурации.
package collectors

import (
	"os"
	"runtime"
	"time"

	"github.com/barysguard/agent/internal/collectors/filewatch"
	"github.com/barysguard/agent/internal/collectors/usb"
	"github.com/barysguard/agent/internal/events"
	"github.com/barysguard/agent/internal/identity"
	"github.com/barysguard/agent/internal/volumes"
)

const defaultPollSeconds = 2

// Platform — всё платформенное, что нужно сборщикам. В тестах подменяется.
type Platform struct {
	Supported    bool
	Provider     volumes.Provider
	Identity     identity.Resolver
	Attributor   filewatch.Attributor
	StartWatcher filewatch.StartWatcher
	Profiles     func() []string
}

func DefaultPlatform() Platform {
	return Platform{
		Supported:    runtime.GOOS == "windows",
		Provider:     volumes.NewProvider(),
		Identity:     identity.New(),
		Attributor:   filewatch.NewAttributor(),
		StartWatcher: filewatch.DefaultStartWatcher,
		Profiles: func() []string {
			return filewatch.ProfileDirs(os.Getenv("SystemDrive") + `\Users`)
		},
	}
}

func enabled(document map[string]any, channel string) bool {
	collectors, _ := document["collectors"].(map[string]any)
	section, _ := collectors[channel].(map[string]any)
	if value, ok := section["enabled"].(bool); ok {
		return value
	}
	return true
}

func pollInterval(document map[string]any) time.Duration {
	collectors, _ := document["collectors"].(map[string]any)
	section, _ := collectors["usb"].(map[string]any)
	if value, ok := section["poll_seconds"].(float64); ok && value >= 1 {
		return time.Duration(value) * time.Second
	}
	return defaultPollSeconds * time.Second
}

// Build строит группу сборщиков по документу. Опрос томов (hub) нужен обоим
// сборщикам и запускается, только если включён хотя бы один из них.
func Build(document map[string]any, dataDir string, plat Platform) []events.Collector {
	if !plat.Supported {
		return nil
	}
	useUSB := enabled(document, "usb")
	cfg := filewatch.ConfigFromDocument(document, plat.Profiles(), dataDir)
	useFiles := cfg.Enabled
	if !useUSB && !useFiles {
		return nil
	}

	hub := volumes.NewHub(plat.Provider, pollInterval(document))
	list := []events.Collector{hub}
	if useUSB {
		list = append(list, usb.New(hub, plat.Identity.ConsoleUser))
	}
	if useFiles {
		list = append(list, filewatch.New(filewatch.Deps{
			Config: cfg, Hub: hub, Identity: plat.Identity,
			Attributor: plat.Attributor, StartWatcher: plat.StartWatcher,
		}))
	}
	return list
}

// NewFactoryFor возвращает фабрику для runner: группа пересобирается по новому
// документу при смене раздела collectors.
func NewFactoryFor(plat Platform, dataDir string) func(map[string]any) []events.Collector {
	return func(document map[string]any) []events.Collector {
		return Build(document, dataDir, plat)
	}
}

func NewFactory(dataDir string) func(map[string]any) []events.Collector {
	return NewFactoryFor(DefaultPlatform(), dataDir)
}
```

- [ ] **Step 5: Правки `agent/internal/runner/events.go` и `agent.go`**

В `Options` (`agent.go`) добавить:

```go
	// CollectorFactory строит сборщики, зависящие от конфигурации. Группа
	// запускается при StartEvents и пересоздаётся, когда меняется раздел
	// collectors документа. Сборщики из Collectors от конфигурации не зависят
	// и не перезапускаются.
	CollectorFactory func(document map[string]any) []events.Collector
```

В `Agent` добавить поля `reload chan map[string]any` и `collectorsDoc any`; в `New` создать `reload: make(chan map[string]any, 1)` в литерале `agent`.

В `events.go`: импорт `reflect`; в `StartEvents` после запуска статических сборщиков и до запуска `drained` добавить:

```go
	if factory := a.options.CollectorFactory; factory != nil {
		document := a.state.Document
		a.collectorsDoc = document["collectors"]
		collectors.Add(1)
		go func() {
			defer collectors.Done()
			a.superviseCollectors(collectorCtx, factory, document)
		}()
	}
```

и новые методы:

```go
// superviseCollectors держит группу сборщиков из фабрики и пересоздаёт её по
// новому документу. Остановка старой группы дожидается возврата всех её
// сборщиков: новая не должна работать параллельно со старой, иначе один том
// наблюдался бы дважды.
func (a *Agent) superviseCollectors(ctx context.Context, factory func(map[string]any) []events.Collector, document map[string]any) {
	var cancelGroup context.CancelFunc
	var group sync.WaitGroup

	startGroup := func(document map[string]any) {
		groupCtx, cancel := context.WithCancel(ctx)
		cancelGroup = cancel
		for _, collector := range factory(document) {
			group.Add(1)
			go func() {
				defer group.Done()
				if err := collector.Run(groupCtx, a.queue.Emit); err != nil {
					slog.Error("сборщик остановился с ошибкой", "collector", collector.Name(), "error", err)
				}
			}()
		}
	}
	stopGroup := func() {
		if cancelGroup != nil {
			cancelGroup()
			group.Wait()
		}
	}

	startGroup(document)
	for {
		select {
		case <-ctx.Done():
			stopGroup()
			return
		case next := <-a.reload:
			stopGroup()
			startGroup(next)
		}
	}
}

// notifyCollectorsReload просит пересоздать группу, если изменился раздел
// collectors. Смена других разделов группу не трогает: перезапуск опроса
// томов продублировал бы usb/mount для уже вставленных флешек.
func (a *Agent) notifyCollectorsReload() {
	if a.options.CollectorFactory == nil || a.options.Buffer == nil {
		return
	}
	section := a.state.Document["collectors"]
	if reflect.DeepEqual(section, a.collectorsDoc) {
		return
	}
	a.collectorsDoc = section
	// Нужен только самый свежий документ: устаревший ожидающий отбрасывается.
	select {
	case <-a.reload:
	default:
	}
	a.reload <- a.state.Document
}
```

В `refreshConfig` и `syncConfig` (`agent.go`) вызвать `a.notifyCollectorsReload()` сразу после `a.emitConfigApplied(...)`.

Замечание по гонке: `a.collectorsDoc` читает и пишет только цикл `Run` (в `RunOnce`) и `StartEvents` до его начала; `reload` — канал, поэтому документ в `superviseCollectors` передаётся без общих переменных.

- [ ] **Step 6: Подключение в `agent/cmd/barysguard-agent/main.go`**

Импорт `"github.com/barysguard/agent/internal/collectors"`; в `runner.Options{...}` внутри `loadAgent` добавить `CollectorFactory: collectors.NewFactory(layout.Dir),`.

- [ ] **Step 7: Запустить все тесты агента**

```bash
cd agent && gofmt -l .; go build ./... && go vet ./... && go test ./... -count=1 -race -timeout 180s 2>&1 | grep -v "^20" | tail -20
GOOS=linux go build ./... && echo linux-ok
```

Expected: PASS.

- [ ] **Step 8: Коммит**

```bash
git add agent
git commit -m "feat(agent): collector factory with config-driven restart; wire file and USB collectors" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Документация, ручная проверка и итоговая проверка

**Files:**
- Create: `docs/COLLECTORS_MANUAL.md`
- Modify: `docs/QUICKSTART.md`, `docs/superpowers/specs/2026-10-05-collectors-files-usb-design.md`

- [ ] **Step 1: `docs/COLLECTORS_MANUAL.md`**

Написать инструкцию ручной проверки (по образцу инструкции в чате: сборка агента, CA стенда, токен, регистрация, запуск **от администратора**), затем сценарии:

1. Подключить флешку → `GET http://localhost:8080/api/v1/events?channel=usb` показывает `mount` с серийным номером, меткой, производителем.
2. Скопировать из «Документов» файл на флешку → `?channel=file&action=copy` показывает событие с `src_path`, `artifact.sha256`, пользователем и (если успел) процессом.
3. Создать файл прямо на флешке → `create` без `src_path`, `severity` medium.
4. Извлечь флешку → `unmount`.
5. Изменить, переименовать и удалить файл в «Документах» → `modify`, `rename` (с `old_path`), `delete`.
6. Скопировать большой файл (> лимита, временно понизив `max_hash_bytes` через конфигурацию группы) → событие без `artifact` с `labels.hash = skipped_size`.
7. Распаковать архив с сотнями файлов → видно `agent/events_dropped` при превышении лимита.
8. Запустить агента без прав администратора → `agent/watch_denied` по папкам чужих профилей, своя папка наблюдается.

Добавить раздел «Если что-то не так» (нет событий: проверить права; повторная регистрация `-force`; кириллица в консоли `chcp 65001`).

- [ ] **Step 2: `docs/QUICKSTART.md`** — раздел «Сборщики файлов и USB»: ссылка на `COLLECTORS_MANUAL.md`, пример изменения раздела `collectors` через `PUT /api/v1/config` (документ конфигурации), пояснение, что на Docker-стенде (Linux) сборщики не включаются.

- [ ] **Step 3: Спека** — статус «реализовано (подпроект 2b-1)»; в §5.6 уточнить: процесс определяется для событий на внешних томах (Restart Manager дорог); в §5.3 — каталоги не порождают событий (определяются при хешировании); в §5.6.1 — отчёт о потерях отправляет сборщик событием `agent/events_dropped` со `subject.component = "filewatch"`; в §10 добавить: уведомления каталогов (удаление каталога выглядит как `file/delete`) — 2b-2.

- [ ] **Step 4: Полный прогон**

```bash
export BG_TEST_DATABASE_URL=postgresql+asyncpg://barysguard:barysguard@localhost:5432/postgres
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test.ps1
```

Expected: все 11 шагов зелёные.

- [ ] **Step 5: Живая проверка на этой машине (без флешки)**

```powershell
cd C:\Users\User\Desktop\BarysGuard\BarysGuard\agent
go build -o C:\BarysGuardTest\barysguard-agent.exe .\cmd\barysguard-agent
# запустить агента (окно администратора не обязательно: будет своя папка и watch_denied для чужих)
Start-Process C:\BarysGuardTest\barysguard-agent.exe -ArgumentList 'run','-data-dir','C:\BarysGuardTest\data' -WindowStyle Hidden
# создать, изменить, переименовать и удалить файл в «Документах» текущего пользователя
$f = "$env:USERPROFILE\Documents\bg-collector-test.txt"
Set-Content $f 'первая версия'; Start-Sleep 3; Add-Content $f 'вторая строка'; Start-Sleep 3
Rename-Item $f bg-collector-test2.txt; Start-Sleep 3; Remove-Item "$env:USERPROFILE\Documents\bg-collector-test2.txt"; Start-Sleep 35
# события в консоли: http://localhost:8080/api/v1/events?channel=file
```

Агент подхватывает раздел `collectors` из конфигурации по умолчанию (она приходит с сервера при регистрации; если агент зарегистрирован до этого цикла, вызовите команду `refresh_config` либо перезапустите его). Expected: события `file/create`, `modify`, `rename` (с `old_path`), `delete` с `artifact.sha256` и пользователем.

- [ ] **Step 6: Коммит**

```bash
git add docs
git commit -m "docs: collectors manual, quickstart section; mark 2b-1 implemented" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Самопроверка плана

**Покрытие спеки.**
- §3 `volumes` (Snapshot, Watch/Hub, классификация по шине, замена носителя в той же букве, replay первого снимка): задачи 2, 3.
- §4 сборщик `usb`: задача 4. §6 пользователь консоли и владелец файла: задача 4 (`identity`) и 5 (`Pick` в конвейере).
- §5.1 наблюдаемые папки и корни внешних томов, `%USERS%`, исключения, каталог данных агента: задачи 5 (`config.go`, `exclude.go`), 6 (`collector.go`).
- §5.2 `ReadDirectoryChangesW`, переполнение, пары переименования: задача 5 (`notify.go`), задача 6 (`watcher_windows.go`, `agent/watch_overflow`).
- §5.3 склейка и стабилизация: задача 5 (`debounce.go`). §5.4 хеширование (занят, больше лимита): `hasher.go`. §5.5 сопоставление `copy`: `hashindex.go`, `pipeline.go`. §5.6 процесс (Restart Manager за интерфейсом `Attributor`): задача 6. §5.6.1 ограничитель: `limiter.go`, `pipeline.go`.
- §6 конфигурация: сервер — задача 1; агент — `ConfigFromDocument`; применение и перезапуск группы — задача 7.
- §7 контракт и проверка на сервере: задачи 1 (сервер), 5 (`BuildEvent`), 4 (`usb.BuildEvent`).
- §8 проверка: юнит-тесты — в каждой задаче, интеграционные Windows — 3, 4, 6; ручная — задача 8; стенд — сборка под Linux проверяется в задачах 3–7 (`GOOS=linux go build`), `smoke.cmd` без изменений.
- §9 риски: процесс неизвестен (метка `labels.process`), шторм (ограничитель), переполнение (`watch_overflow`), занятый файл (`unavailable`), нет прав (`watch_denied`), короткое подключение (интервал настраиваем).

**Отклонения от спеки (фиксируются в задаче 8):** процесс определяется только для событий на внешних томах; каталоги отсеиваются при хешировании (событий `file/delete` для каталогов избежать нельзя — это ограничение уведомлений ОС).

**Согласованность имён.** `Hub.Subscribe/Poll/Current`, `volumes.Change`, `usb.New(hub, consoleUser)`, `identity.Resolver/Pick/IsServiceSID`, `filewatch.Deps/New/Pipeline/Config/Raw/Kind`, `collectors.Platform/Build/NewFactory/NewFactoryFor`, `runner.Options.CollectorFactory`, `Agent.superviseCollectors/notifyCollectorsReload` используются одинаково во всех задачах.
