"""Разбор пакета NDJSON без базы данных."""

import json
import uuid

import pytest

from barysguard.services.events import BatchTooLargeError, BatchUnreadableError, parse_batch


def _event(**overrides) -> dict:
    event = {
        "event_id": str(uuid.uuid4()),
        "schema_version": 1,
        "occurred_at": "2026-10-05T10:00:00+06:00",
        "channel": "agent",
        "action": "start",
    }
    event.update(overrides)
    return event


def _body(*lines: str) -> bytes:
    return "\n".join(lines).encode("utf-8")


def test_valid_event_is_parsed_with_defaults() -> None:
    parsed = parse_batch(_body(json.dumps(_event())), max_lines=10)

    assert len(parsed.events) == 1
    line, envelope = parsed.events[0]
    assert line == 1
    assert envelope.severity_hint.value == "info"
    assert envelope.subject == {}
    assert parsed.rejected == []


def test_each_defect_is_reported_with_its_line_number() -> None:
    body = _body(
        "{не json",
        json.dumps([1, 2]),
        json.dumps(_event(schema_version=2)),
        json.dumps(_event(channel="telepathy")),
        json.dumps(_event(occurred_at="2026-10-05T10:00:00")),
        json.dumps(_event(action="")),
        json.dumps(_event(labels="не объект")),
        json.dumps(_event()),
    )

    parsed = parse_batch(body, max_lines=100)

    assert [(r.line, r.reason) for r in parsed.rejected] == [
        (1, "invalid_json"),
        (2, "not_an_object"),
        (3, "unsupported_schema"),
        (4, "invalid_event"),
        (5, "invalid_event"),
        (6, "invalid_event"),
        (7, "invalid_event"),
    ]
    assert [line for line, _ in parsed.events] == [8]


def test_blank_lines_and_crlf_are_tolerated_and_keep_line_numbers() -> None:
    body = (json.dumps(_event()) + "\r\n\r\n" + json.dumps(_event(channel="x")) + "\r\n").encode()

    parsed = parse_batch(body, max_lines=10)

    assert [line for line, _ in parsed.events] == [1]
    assert [(r.line, r.reason) for r in parsed.rejected] == [(3, "invalid_event")]


def test_oversized_event_is_rejected_without_hurting_neighbours() -> None:
    huge = _event(labels={"blob": "x" * 70_000})

    parsed = parse_batch(_body(json.dumps(huge), json.dumps(_event())), max_lines=10)

    assert [(r.line, r.reason) for r in parsed.rejected] == [(1, "too_large")]
    assert [line for line, _ in parsed.events] == [2]


def test_agent_id_in_body_is_ignored() -> None:
    parsed = parse_batch(_body(json.dumps(_event(agent_id=str(uuid.uuid4())))), max_lines=10)

    assert not hasattr(parsed.events[0][1], "agent_id")


def test_too_many_lines_is_refused() -> None:
    with pytest.raises(BatchTooLargeError):
        parse_batch(_body(*[json.dumps(_event()) for _ in range(3)]), max_lines=2)


def test_undecodable_or_empty_body_is_unreadable() -> None:
    with pytest.raises(BatchUnreadableError):
        parse_batch(b"\xff\xfe\x00", max_lines=10)
    with pytest.raises(BatchUnreadableError):
        parse_batch(b"\n  \n", max_lines=10)


@pytest.mark.parametrize(
    "overrides",
    [
        {"occurred_at": "9999-12-31T23:59:59-05:00"},
        {"occurred_at": "0001-01-01T00:00:00+05:00"},
        {"labels": {"clip": "a\u0000b"}},
        {"subject": {"nested": [{"k\u0000ey": 1}]}},
        {"labels": {"lone": "\ud800"}},
        {"action": "st\u0000art"},
    ],
    ids=["max-time", "min-time", "nul-in-value", "nul-in-key", "lone-surrogate", "nul-in-action"],
)
def test_values_the_database_cannot_store_are_rejected_per_event(overrides: dict) -> None:
    # Такие значения проходят pydantic, но роняли бы INSERT всего пакета и
    # навсегда останавливали отправку событий этого агента.
    body = _body(json.dumps(_event(**overrides)), json.dumps(_event()))

    parsed = parse_batch(body, max_lines=10)

    assert [(r.line, r.reason) for r in parsed.rejected] == [(1, "invalid_event")]
    assert [line for line, _ in parsed.events] == [2]
