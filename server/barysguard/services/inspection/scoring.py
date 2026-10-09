"""Оценка находок и пороги вердикта."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

FLAG_SCORE = 20


@dataclass(frozen=True)
class RuleWeight:
    key: str
    rule_version_id: str
    weight: int
    cap: int
    title: str = ""


@dataclass(frozen=True)
class VerdictResult:
    status: str
    severity: str
    score: int
    matches: list[dict[str, Any]]


def classify(score: int) -> tuple[str, str]:
    """(статус, критичность) по итоговой оценке."""
    if score < FLAG_SCORE:
        return "clean", "info"
    if score < 50:
        return "flagged", "medium"
    if score < 80:
        return "flagged", "high"
    return "flagged", "critical"


def evaluate(findings: dict[str, dict[str, Any]], weights: Sequence[RuleWeight]) -> VerdictResult:
    """Вклад правила — вес × min(число совпадений, потолок); итог не больше 100."""
    matches: list[dict[str, Any]] = []
    total = 0
    for rule in weights:
        found = findings.get(rule.key)
        if not found or found.get("count", 0) <= 0:
            continue
        count = int(found["count"])
        points = rule.weight * min(count, rule.cap)
        total += points
        matches.append(
            {
                "rule_key": rule.key,
                "rule_title": rule.title,
                "rule_version_id": rule.rule_version_id,
                "count": count,
                "points": points,
                "samples": list(found.get("samples", [])),
            }
        )
    score = min(total, 100)
    status, severity = classify(score)
    return VerdictResult(status=status, severity=severity, score=score, matches=matches)
