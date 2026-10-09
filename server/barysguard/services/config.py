import hashlib
import ipaddress
import json
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.errors import ConfigTreeError
from barysguard.db.models.agent import Agent, AgentGroup
from barysguard.db.models.config import AgentConfig, ConfigScope

# Дерево групп строят операторы, и цикл parent_id не исключён полностью.
MAX_GROUP_DEPTH = 32


class TransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heartbeat_interval_seconds: int = Field(default=30, ge=5, le=3600)
    event_batch_max: int = Field(default=500, ge=1, le=10000)
    event_batch_max_bytes: int = Field(default=4 * 1024 * 1024, ge=64 * 1024, le=64 * 1024 * 1024)
    backoff_base_seconds: int = Field(default=1, ge=1, le=60)
    backoff_max_seconds: int = Field(default=300, ge=1, le=3600)


class BufferConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_bytes: int = Field(default=500 * 1024 * 1024, ge=1024 * 1024)
    max_age_days: int = Field(default=7, ge=1, le=365)


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["debug", "info", "warn", "error"] = "info"


class UsbCollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    poll_seconds: int = Field(default=2, ge=1, le=60)


def _default_watch_paths() -> list[str]:
    return [r"%USERS%\Documents", r"%USERS%\Desktop", r"%USERS%\Downloads"]


def _default_watch_exclude() -> list[str]:
    return [r"*\~$*", "*.tmp", "*.crdownload", r"*\AppData\*"]


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


class ArtifactCollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    staging_max_bytes: int = Field(
        default=500 * 1024 * 1024, ge=1024 * 1024, le=50 * 1024 * 1024 * 1024
    )
    upload_bytes_per_second: int = Field(default=2 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    stage_bytes_per_minute: int = Field(
        default=200 * 1024 * 1024, ge=1024 * 1024, le=100 * 1024 * 1024 * 1024
    )


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


class CollectorsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usb: UsbCollectorConfig = UsbCollectorConfig()
    file_watch: FileWatchConfig = FileWatchConfig()
    artifact: ArtifactCollectorConfig = ArtifactCollectorConfig()
    net_upload: NetUploadCollectorConfig = NetUploadCollectorConfig()


class AgentConfigDocument(BaseModel):
    """Полная форма конфигурации агента.

    extra="forbid" на каждом уровне обязателен: опечатка вроде
    heartbeat_intervall иначе молча не применится, и разбор такого
    инцидента занимает часы.
    """

    model_config = ConfigDict(extra="forbid")

    transport: TransportConfig = TransportConfig()
    buffer: BufferConfig = BufferConfig()
    logging: LoggingConfig = LoggingConfig()
    collectors: CollectorsConfig = CollectorsConfig()
    # Наполняется подпроектом 3. Зарезервирован пустым, чтобы добавление
    # политик не меняло версию контракта.
    policies: dict[str, Any] = Field(default_factory=dict)


def merge_documents(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Слияние вложенных словарей. Списки заменяются целиком.

    Дополнение списков сделало бы невыразимой операцию «убрать один путь
    из унаследованных исключений»: дочерняя группа могла бы только добавлять.
    """
    result = dict(base)
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            result[key] = merge_documents(current, value)
        else:
            result[key] = value
    return result


def compute_config_version(document: dict[str, Any]) -> int:
    """Версия — хеш документа, а не счётчик.

    Счётчик потребовал бы UPDATE по всем агентам при правке корневой строки.
    При хеше правка не пишет в agents ни одной строки: агент обнаруживает
    расхождение сам на ближайшем heartbeat.
    """
    material = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


async def group_chain(session: AsyncSession, group_id: uuid.UUID) -> list[uuid.UUID]:
    """Цепочка групп от корня дерева к указанной."""
    chain: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    current: uuid.UUID | None = group_id

    while current is not None:
        if current in seen:
            raise ConfigTreeError(f"cycle in agent group tree at {current}")
        if len(chain) >= MAX_GROUP_DEPTH:
            raise ConfigTreeError(f"agent group tree deeper than {MAX_GROUP_DEPTH}")
        seen.add(current)
        chain.append(current)
        current = (
            await session.execute(select(AgentGroup.parent_id).where(AgentGroup.id == current))
        ).scalar_one_or_none()

    chain.reverse()
    return chain


async def effective_document(session: AsyncSession, group_id: uuid.UUID | None) -> dict[str, Any]:
    """Документ агента: значения по умолчанию, глобальная строка, затем группы.

    Отсчёт от значений по умолчанию, а не от глобальной строки, намеренный:
    сервер обязан отдавать осмысленный конфиг и на пустой базе.
    """
    document = AgentConfigDocument().model_dump(mode="json")

    global_row = (
        await session.execute(select(AgentConfig).where(AgentConfig.scope == ConfigScope.GLOBAL))
    ).scalar_one_or_none()
    if global_row is not None:
        document = merge_documents(document, global_row.document)

    if group_id is None:
        return document

    chain = await group_chain(session, group_id)
    rows = (
        (await session.execute(select(AgentConfig).where(AgentConfig.group_id.in_(chain))))
        .scalars()
        .all()
    )
    by_group = {row.group_id: row for row in rows}
    for node in chain:
        row = by_group.get(node)
        if row is not None:
            document = merge_documents(document, row.document)

    return document


async def effective_config_for_agent(
    session: AsyncSession, agent: Agent
) -> tuple[dict[str, Any], int]:
    document = await effective_document(session, agent.group_id)
    return document, compute_config_version(document)


async def global_heartbeat_interval(session: AsyncSession) -> int:
    """Интервал из глобальной конфигурации.

    Используется там, где эффективный документ на каждого агента обошёлся бы
    обходом дерева групп на каждой строке списка.
    """
    document = await effective_document(session, None)
    return int(document["transport"]["heartbeat_interval_seconds"])
