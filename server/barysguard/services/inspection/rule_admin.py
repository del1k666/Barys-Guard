"""Управление правилами инспекции из консоли: проверка, версии, термины.

Правки идут под блокировкой строки правила; каждое изменение параметров (вес, потолок,
шаблон, термины) публикует новую неизменяемую версию. Тестовый текст нигде не сохраняется.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.config import Settings
from barysguard.db.models.inspection import Dictionary, DictionaryTerm, Rule, RuleVersion
from barysguard.services.inspection.detectors import DictionaryDetector, normalize
from barysguard.services.inspection.regex_detector import PatternError, compile_pattern
from barysguard.services.inspection.rules import terms_hash

MAX_TITLE = 120
MAX_TERM = 200
MAX_TERMS_PER_CALL = 500
MAX_TEST_MATCHES = 200
_LAST_RULE_LOCK = 4_815_162_342  # ключ advisory-блокировки отключения правил


class RuleError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class RuleView:
    id: uuid.UUID
    key: str
    kind: str
    title: str
    builtin: bool
    enabled: bool
    version: int
    weight: int
    cap: int
    pattern: str | None
    ignore_case: bool
    terms_count: int | None
    updated_at: datetime


@dataclass(frozen=True)
class TestOutcome:
    ok: bool
    error: str | None
    count: int
    matches: list[tuple[int, int]]


# --- проверка значений ----------------------------------------------------


def _int(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RuleError(422, f"{name}: целое число от {low} до {high}")
    return value


def _title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    if not title or len(title) > MAX_TITLE:
        raise RuleError(422, f"Название: от 1 до {MAX_TITLE} символов")
    return title


def clean_terms(terms: list[str] | None) -> list[str]:
    """Обрезает пробелы, отбрасывает пустые и повторы (без учёта регистра и ё/е)."""
    if terms is not None and len(terms) > MAX_TERMS_PER_CALL:
        raise RuleError(422, f"За один раз можно добавить не больше {MAX_TERMS_PER_CALL} терминов")
    seen: dict[str, str] = {}
    for raw in terms or []:
        term = " ".join(str(raw).split())
        if not term:
            continue
        if len(term) > MAX_TERM:
            raise RuleError(422, f"Термин длиннее {MAX_TERM} символов")
        seen.setdefault(normalize(term), term)
    return list(seen.values())


def check_pattern(pattern: Any, ignore_case: bool, max_pattern: int) -> str:
    if not isinstance(pattern, str) or not pattern.strip():
        raise RuleError(422, "Укажите шаблон")
    if len(pattern) > max_pattern:
        raise RuleError(422, f"Шаблон длиннее {max_pattern} символов")
    try:
        compiled = compile_pattern(pattern, ignore_case)
    except PatternError as exc:
        raise RuleError(422, exc.message) from None
    if compiled.search("") is not None:
        raise RuleError(422, "Шаблон совпадает с пустой строкой: уточните его")
    return pattern


def _positions(pattern: str, ignore_case: bool, text: str, max_match: int) -> list[tuple[int, int]]:
    compiled = compile_pattern(pattern, ignore_case)
    result: list[tuple[int, int]] = []
    for match in compiled.finditer(text):
        length = match.end() - match.start()
        if 0 < length <= max_match:
            result.append((match.start(), match.end()))
    return result


def _term_positions(terms: list[str], text: str) -> list[tuple[int, int]]:
    detector = DictionaryDetector("test", terms)
    result: list[tuple[int, int]] = []
    for match in detector.pattern.finditer(text):
        result.append((match.start(), match.end()))
    return result


def run_rule_test(
    kind: str,
    *,
    pattern: str | None,
    ignore_case: bool,
    terms: list[str] | None,
    text: str,
    max_pattern: int,
    max_match: int,
    max_text: int,
) -> TestOutcome:
    """Проверка правила на тексте оператора; текст не хранится, возвращаются позиции."""
    if len(text) > max_text:
        raise RuleError(422, f"Тестовый текст длиннее {max_text} символов")
    try:
        if kind == "regex":
            check_pattern(pattern, ignore_case, max_pattern)
            positions = _positions(str(pattern), ignore_case, text, max_match)
        elif kind == "dictionary":
            positions = _term_positions(clean_terms(terms), text)
        else:
            raise RuleError(422, "Тип правила: словарь или шаблон")
    except RuleError as exc:
        if exc.status_code == 422 and kind == "regex":
            return TestOutcome(False, exc.message, 0, [])
        raise
    return TestOutcome(True, None, len(positions), positions[:MAX_TEST_MATCHES])


def _requires_match(pattern: str, ignore_case: bool, test_text: Any, settings: Settings) -> None:
    if not isinstance(test_text, str) or not test_text:
        raise RuleError(422, "Проверьте шаблон на тестовом тексте перед сохранением")
    if len(test_text) > settings.rules_test_max_text:
        raise RuleError(422, f"Тестовый текст длиннее {settings.rules_test_max_text} символов")
    if not _positions(pattern, ignore_case, test_text, settings.regex_max_match):
        raise RuleError(422, "На тестовом тексте нет совпадений: проверьте шаблон")


# --- чтение ----------------------------------------------------------------


async def _latest(session: AsyncSession, rule_id: uuid.UUID) -> RuleVersion:
    version = await session.scalar(
        select(RuleVersion)
        .where(RuleVersion.rule_id == rule_id)
        .order_by(RuleVersion.version.desc())
        .limit(1)
    )
    if version is None:
        raise RuleError(404, "Правило не найдено")
    return version


async def _terms_count(session: AsyncSession, params: dict[str, Any]) -> int | None:
    if "dictionary_id" not in params:
        return None
    return int(
        await session.scalar(
            select(func.count())
            .select_from(DictionaryTerm)
            .where(DictionaryTerm.dictionary_id == uuid.UUID(params["dictionary_id"]))
        )
        or 0
    )


async def _view(session: AsyncSession, rule: Rule, version: RuleVersion) -> RuleView:
    params = version.params
    return RuleView(
        id=rule.id,
        key=rule.key,
        kind=rule.kind,
        title=rule.title,
        builtin=rule.builtin,
        enabled=rule.enabled,
        version=version.version,
        weight=int(params["weight"]),
        cap=int(params["cap"]),
        pattern=params.get("pattern"),
        ignore_case=bool(params.get("ignore_case", False)),
        terms_count=await _terms_count(session, params),
        updated_at=rule.updated_at,
    )


async def list_rules(session: AsyncSession) -> list[RuleView]:
    latest = (
        select(RuleVersion.rule_id, func.max(RuleVersion.version).label("version"))
        .group_by(RuleVersion.rule_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Rule, RuleVersion)
            .join(latest, latest.c.rule_id == Rule.id)
            .join(
                RuleVersion,
                and_(
                    RuleVersion.rule_id == latest.c.rule_id,
                    RuleVersion.version == latest.c.version,
                ),
            )
            .order_by(Rule.builtin.desc(), Rule.created_at, Rule.key)
        )
    ).all()
    return [await _view(session, rule, version) for rule, version in rows]


async def _rule(session: AsyncSession, rule_id: uuid.UUID, *, lock: bool = False) -> Rule:
    statement = select(Rule).where(Rule.id == rule_id)
    if lock:
        statement = statement.with_for_update()
    rule = await session.scalar(statement.execution_options(populate_existing=True))
    if rule is None:
        raise RuleError(404, "Правило не найдено")
    return rule


async def get_rule(session: AsyncSession, rule_id: uuid.UUID) -> RuleView:
    rule = await _rule(session, rule_id)
    return await _view(session, rule, await _latest(session, rule.id))


async def list_versions(session: AsyncSession, rule_id: uuid.UUID) -> list[RuleVersion]:
    await _rule(session, rule_id)
    return list(
        (
            await session.scalars(
                select(RuleVersion)
                .where(RuleVersion.rule_id == rule_id)
                .order_by(RuleVersion.version.desc())
            )
        ).all()
    )


# --- запись ----------------------------------------------------------------


async def _publish(session: AsyncSession, rule: Rule, params: dict[str, Any]) -> RuleVersion:
    """Новая версия, если параметры изменились; иначе возвращается последняя."""
    latest = await _latest(session, rule.id)
    if latest.params == params:
        return latest
    version = RuleVersion(rule_id=rule.id, version=latest.version + 1, params=params)
    session.add(version)
    rule.updated_at = datetime.now(UTC)
    await session.flush()
    return version


async def create_rule(
    session: AsyncSession,
    settings: Settings,
    *,
    kind: str,
    title: str,
    weight: int,
    cap: int,
    pattern: str | None,
    ignore_case: bool,
    test_text: str | None,
    terms: list[str] | None,
) -> RuleView:
    if kind not in ("dictionary", "regex"):
        raise RuleError(422, "Тип правила: словарь или шаблон")
    title = _title(title)
    weight = _int("Вес", weight, 1, 100)
    cap = _int("Потолок", cap, 1, 50)
    key = f"custom_{uuid.uuid4().hex[:8]}"

    if kind == "regex":
        checked = check_pattern(pattern, ignore_case, settings.regex_max_pattern)
        _requires_match(checked, ignore_case, test_text, settings)
        rule = Rule(key=key, kind="regex", title=title, builtin=False)
        session.add(rule)
        await session.flush()
        params: dict[str, Any] = {
            "pattern": checked,
            "ignore_case": bool(ignore_case),
            "weight": weight,
            "cap": cap,
        }
    else:
        cleaned = clean_terms(terms)
        dictionary = Dictionary(key=key, title=title)
        session.add(dictionary)
        await session.flush()
        session.add_all(DictionaryTerm(dictionary_id=dictionary.id, term=t) for t in cleaned)
        rule = Rule(key=key, kind="dictionary", title=title, builtin=False)
        session.add(rule)
        await session.flush()
        params = {
            "weight": weight,
            "cap": cap,
            "dictionary_id": str(dictionary.id),
            "terms_hash": terms_hash(cleaned),
        }

    session.add(RuleVersion(rule_id=rule.id, version=1, params=params))
    await session.flush()
    return await get_rule(session, rule.id)


_NOT_NULLABLE = ("title", "enabled", "weight", "cap", "pattern", "ignore_case")


async def update_rule(
    session: AsyncSession, settings: Settings, rule_id: uuid.UUID, fields: dict[str, Any]
) -> tuple[RuleView, dict[str, Any]]:
    for name in _NOT_NULLABLE:
        if name in fields and fields[name] is None:
            raise RuleError(422, f"Поле «{name}» нельзя сбросить в пустое значение")
    rule = await _rule(session, rule_id, lock=True)
    latest = await _latest(session, rule.id)
    params = dict(latest.params)
    changes: dict[str, Any] = {}

    if "title" in fields:
        new_title = _title(fields["title"])
        if new_title != rule.title:
            changes["title"] = [rule.title, new_title]
            rule.title = new_title

    if "enabled" in fields:
        enabled = bool(fields["enabled"])
        if enabled != rule.enabled:
            if not enabled:
                # Два одновременных отключения разных правил не должны опустошить набор:
                # проверка идёт под общей транзакционной блокировкой (READ COMMITTED
                # увидит уже зафиксированное отключение соседа).
                await session.execute(select(func.pg_advisory_xact_lock(_LAST_RULE_LOCK)))
                others = await session.scalar(
                    select(func.count())
                    .select_from(Rule)
                    .where(Rule.enabled.is_(True), Rule.id != rule.id)
                )
                if not others:
                    raise RuleError(409, "Нельзя отключить последнее включённое правило")
            changes["enabled"] = [rule.enabled, enabled]
            rule.enabled = enabled

    for name, label, low, high in (("weight", "Вес", 1, 100), ("cap", "Потолок", 1, 50)):
        if name in fields:
            value = _int(label, fields[name], low, high)
            if value != params[name]:
                changes[name] = [params[name], value]
                params[name] = value

    if "pattern" in fields or "ignore_case" in fields:
        if rule.kind != "regex" or rule.builtin:
            raise RuleError(422, "Шаблон есть только у собственных правил-шаблонов")
        pattern = fields.get("pattern", params["pattern"])
        ignore_case = bool(fields.get("ignore_case", params.get("ignore_case", False)))
        if pattern != params["pattern"] or ignore_case != params.get("ignore_case", False):
            checked = check_pattern(pattern, ignore_case, settings.regex_max_pattern)
            _requires_match(checked, ignore_case, fields.get("test_text"), settings)
            if checked != params["pattern"]:
                changes["pattern"] = [params["pattern"], checked]
            if ignore_case != params.get("ignore_case", False):
                changes["ignore_case"] = [params.get("ignore_case", False), ignore_case]
            params["pattern"] = checked
            params["ignore_case"] = ignore_case

    if params != latest.params:
        await _publish(session, rule, params)
    elif changes:
        rule.updated_at = datetime.now(UTC)
    await session.flush()
    return await get_rule(session, rule.id), changes


# --- термины ----------------------------------------------------------------


async def _dictionary_rule(
    session: AsyncSession, rule_id: uuid.UUID, *, lock: bool
) -> tuple[Rule, uuid.UUID]:
    rule = await _rule(session, rule_id, lock=lock)
    if rule.kind != "dictionary":
        raise RuleError(422, "Термины есть только у словарных правил")
    latest = await _latest(session, rule.id)
    return rule, uuid.UUID(latest.params["dictionary_id"])


async def _republish_terms(session: AsyncSession, rule: Rule, dictionary_id: uuid.UUID) -> None:
    terms = (
        await session.scalars(
            select(DictionaryTerm.term).where(DictionaryTerm.dictionary_id == dictionary_id)
        )
    ).all()
    latest = await _latest(session, rule.id)
    await _publish(session, rule, {**latest.params, "terms_hash": terms_hash(terms)})


async def add_terms(session: AsyncSession, rule_id: uuid.UUID, terms: list[str]) -> int:
    rule, dictionary_id = await _dictionary_rule(session, rule_id, lock=True)
    cleaned = clean_terms(terms)
    existing = {
        normalize(t)
        for t in (
            await session.scalars(
                select(DictionaryTerm.term).where(DictionaryTerm.dictionary_id == dictionary_id)
            )
        ).all()
    }
    fresh = [t for t in cleaned if normalize(t) not in existing]
    session.add_all(DictionaryTerm(dictionary_id=dictionary_id, term=t) for t in fresh)
    await session.flush()
    if fresh:
        await _republish_terms(session, rule, dictionary_id)
    return len(fresh)


async def delete_term(session: AsyncSession, rule_id: uuid.UUID, term_id: uuid.UUID) -> None:
    rule, dictionary_id = await _dictionary_rule(session, rule_id, lock=True)
    result = await session.execute(
        delete(DictionaryTerm).where(
            DictionaryTerm.id == term_id, DictionaryTerm.dictionary_id == dictionary_id
        )
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise RuleError(404, "Термин не найден")
    await session.flush()
    await _republish_terms(session, rule, dictionary_id)


async def list_terms(
    session: AsyncSession, rule_id: uuid.UUID, q: str | None, limit: int, offset: int
) -> tuple[list[DictionaryTerm], int]:
    _, dictionary_id = await _dictionary_rule(session, rule_id, lock=False)
    conditions = [DictionaryTerm.dictionary_id == dictionary_id]
    if q:
        conditions.append(DictionaryTerm.term.ilike(f"%{q}%"))
    total = int(
        await session.scalar(select(func.count()).select_from(DictionaryTerm).where(*conditions))
        or 0
    )
    items = (
        await session.scalars(
            select(DictionaryTerm)
            .where(*conditions)
            .order_by(DictionaryTerm.term)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return list(items), total
