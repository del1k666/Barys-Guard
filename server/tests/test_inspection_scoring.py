"""Оценка и пороги вердикта."""

import pytest

from barysguard.services.inspection.scoring import RuleWeight, classify, evaluate

WEIGHTS = [
    RuleWeight("iin_bin", "v-iin", weight=20, cap=5),
    RuleWeight("card", "v-card", weight=25, cap=4),
    RuleWeight("markings", "v-mark", weight=15, cap=2),
]


def _findings(**counts: int) -> dict:
    return {key: {"count": n, "samples": ["***"]} for key, n in counts.items()}


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, ("clean", "info")),
        (19, ("clean", "info")),
        (20, ("flagged", "medium")),
        (49, ("flagged", "medium")),
        (50, ("flagged", "high")),
        (79, ("flagged", "high")),
        (80, ("flagged", "critical")),
        (100, ("flagged", "critical")),
    ],
)
def test_thresholds(score: int, expected: tuple[str, str]) -> None:
    assert classify(score) == expected


def test_contribution_is_weight_times_capped_count() -> None:
    # Пара не упирается в общий потолок 100: счёт 9 режется до cap=2.
    result = evaluate(_findings(markings=9, card=1), WEIGHTS)

    assert result.score == 15 * 2 + 25 * 1
    assert {m["rule_key"]: m["points"] for m in result.matches} == {"markings": 30, "card": 25}


def test_total_is_capped_at_one_hundred() -> None:
    result = evaluate(_findings(iin_bin=5, card=4, markings=2), WEIGHTS)

    assert result.score == 100
    assert (result.status, result.severity) == ("flagged", "critical")


def test_clean_when_nothing_found() -> None:
    result = evaluate({}, WEIGHTS)

    assert (result.status, result.severity, result.score, result.matches) == (
        "clean",
        "info",
        0,
        [],
    )


def test_matches_carry_rule_version_and_masked_samples() -> None:
    result = evaluate(_findings(markings=1), WEIGHTS)

    assert result.matches == [
        {
            "rule_key": "markings",
            "rule_version_id": "v-mark",
            "count": 1,
            "points": 15,
            "samples": ["***"],
        }
    ]
    assert result.status == "clean"  # 15 < 20


def test_findings_of_unknown_rules_are_ignored() -> None:
    assert evaluate(_findings(other=3), WEIGHTS).score == 0
