"""Правила инспекции: встроенный набор и загрузка действующих версий."""

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.inspection import Dictionary, DictionaryTerm, Rule, RuleVersion
from barysguard.services.inspection.detectors import (
    CardDetector,
    Detector,
    DictionaryDetector,
    IinBinDetector,
)
from barysguard.services.inspection.scoring import RuleWeight

MARKINGS_KEY = "markings"

BUILTIN_TERMS = (
    "конфиденциально",
    "строго конфиденциально",
    "для служебного пользования",
    "коммерческая тайна",
    "служебная тайна",
    "секретно",
    "не для распространения",
)

BUILTIN_RULES: tuple[dict[str, Any], ...] = (
    {
        "key": "iin_bin",
        "kind": "detector",
        "title": "ИИН/БИН (Казахстан)",
        "params": {"detector": "iin_bin", "weight": 20, "cap": 5},
    },
    {
        "key": "card",
        "kind": "detector",
        "title": "Банковские карты",
        "params": {"detector": "card", "weight": 25, "cap": 4},
    },
    {
        "key": MARKINGS_KEY,
        "kind": "dictionary",
        "title": "Грифы конфиденциальности",
        "params": {"weight": 15, "cap": 2},
    },
)


def terms_hash(terms: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(terms)).encode()).hexdigest()


@dataclass(frozen=True)
class SeedReport:
    rules_created: int
    versions_created: int
    terms_added: int


async def seed_rules(session: AsyncSession) -> SeedReport:
    """Заводит встроенные правила и словарь. Повторный вызов ничего не дублирует.

    Термины словаря добавляются только при его создании; после правки терминов
    оператором повторный запуск публикует новую версию правила (сменился terms_hash).
    Параметры возвращаются к встроенным: ручные правки весов (когда появится
    редактор) повторный запуск перезапишет новой версией.
    """
    added: list[str] = []
    dictionary = await session.scalar(select(Dictionary).where(Dictionary.key == MARKINGS_KEY))
    if dictionary is None:
        # Встроенные термины кладутся только при создании словаря: дальше словарь
        # принадлежит оператору, и удалённый им термин повторный запуск не возвращает.
        dictionary = Dictionary(key=MARKINGS_KEY, title="Грифы")
        session.add(dictionary)
        await session.flush()
        added = list(BUILTIN_TERMS)
        session.add_all(DictionaryTerm(dictionary_id=dictionary.id, term=term) for term in added)
        await session.flush()

    terms = (
        await session.scalars(
            select(DictionaryTerm.term).where(DictionaryTerm.dictionary_id == dictionary.id)
        )
    ).all()
    digest = terms_hash(terms)

    rules_created = versions_created = 0
    for definition in BUILTIN_RULES:
        rule = await session.scalar(select(Rule).where(Rule.key == definition["key"]))
        if rule is None:
            rule = Rule(key=definition["key"], kind=definition["kind"], title=definition["title"])
            session.add(rule)
            await session.flush()
            rules_created += 1

        params = dict(definition["params"])
        if rule.kind == "dictionary":
            params["dictionary_id"] = str(dictionary.id)
            params["terms_hash"] = digest

        latest = await session.scalar(
            select(RuleVersion)
            .where(RuleVersion.rule_id == rule.id)
            .order_by(RuleVersion.version.desc())
            .limit(1)
        )
        if latest is None or latest.params != params:
            session.add(
                RuleVersion(
                    rule_id=rule.id,
                    version=latest.version + 1 if latest else 1,
                    params=params,
                )
            )
            versions_created += 1

    await session.flush()
    return SeedReport(rules_created, versions_created, len(added))


@dataclass(frozen=True)
class RuleRuntime:
    key: str
    kind: str
    detector: str
    rule_version_id: str
    weight: int
    cap: int
    terms: tuple[str, ...]


@dataclass(frozen=True)
class Ruleset:
    hash: str
    rules: tuple[RuleRuntime, ...]

    def detectors(self) -> list[Detector]:
        result: list[Detector] = []
        for rule in self.rules:
            if rule.kind == "dictionary":
                result.append(DictionaryDetector(rule.key, rule.terms))
            elif rule.detector == "iin_bin":
                result.append(IinBinDetector(rule.key))
            elif rule.detector == "card":
                result.append(CardDetector(rule.key))
        return result

    def weights(self) -> list[RuleWeight]:
        return [RuleWeight(r.key, r.rule_version_id, r.weight, r.cap) for r in self.rules]

    def version_ids(self) -> dict[str, str]:
        return {rule.key: rule.rule_version_id for rule in self.rules}


async def load_ruleset(session: AsyncSession) -> Ruleset:
    """Последние версии включённых правил и хеш всего набора (с учётом терминов словарей)."""
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
            .where(Rule.enabled.is_(True))
            .order_by(Rule.key)
        )
    ).all()

    runtime: list[RuleRuntime] = []
    lines: list[str] = []
    for rule, version in rows:
        terms: tuple[str, ...] = ()
        if rule.kind == "dictionary":
            dictionary_id = uuid.UUID(version.params["dictionary_id"])
            terms = tuple(
                sorted(
                    (
                        await session.scalars(
                            select(DictionaryTerm.term).where(
                                DictionaryTerm.dictionary_id == dictionary_id
                            )
                        )
                    ).all()
                )
            )
        lines.append(f"{version.id}:{terms_hash(terms)}")
        runtime.append(
            RuleRuntime(
                key=rule.key,
                kind=rule.kind,
                detector=str(version.params.get("detector", "")),
                rule_version_id=str(version.id),
                weight=int(version.params["weight"]),
                cap=int(version.params["cap"]),
                terms=terms,
            )
        )
    digest = hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()
    return Ruleset(hash=digest, rules=tuple(runtime))
