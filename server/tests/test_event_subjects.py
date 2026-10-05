"""Проверка subject для каналов file и usb."""

import json
import uuid

import pytest

from barysguard.gateway.event_schemas import Channel
from barysguard.services.event_subjects import subject_is_valid
from barysguard.services.events import parse_batch

VOLUME = {"type": "removable", "serial": "0781-5583", "label": "KINGSTON", "fs": "NTFS"}


def _file(**subject) -> dict:
    return {"dst_path": "E:\\report.xlsx", "volume": VOLUME, **subject}


@pytest.mark.parametrize(
    ("action", "subject"),
    [
        ("create", _file()),
        ("modify", _file(size_bytes=10)),
        ("delete", _file()),
        ("rename", _file(old_path="E:\\old.xlsx")),
        ("copy", _file(src_path="C:\\Users\\u\\Documents\\report.xlsx")),
    ],
)
def test_valid_file_subjects(action: str, subject: dict) -> None:
    assert subject_is_valid(Channel.FILE, action, subject) is True


@pytest.mark.parametrize(
    ("action", "subject"),
    [
        ("teleport", _file()),
        ("create", {"volume": VOLUME}),
        ("create", {"dst_path": "", "volume": VOLUME}),
        ("create", {"dst_path": "E:\\x"}),
        ("create", {"dst_path": "E:\\x", "volume": {"type": "cloud"}}),
        ("rename", _file()),
        ("copy", _file()),
        ("create", _file(size_bytes=-1)),
        ("create", _file(size_bytes=True)),
    ],
)
def test_invalid_file_subjects(action: str, subject: dict) -> None:
    assert subject_is_valid(Channel.FILE, action, subject) is False


def test_usb_subjects() -> None:
    good = {"drive_letter": "E:", "volume": VOLUME, "device": {"bus": "usb"}}
    without_volume = {k: v for k, v in good.items() if k != "volume"}

    assert subject_is_valid(Channel.USB, "mount", good) is True
    assert subject_is_valid(Channel.USB, "unmount", good) is True
    assert subject_is_valid(Channel.USB, "eject", good) is False
    assert subject_is_valid(Channel.USB, "mount", {**good, "drive_letter": ""}) is False
    assert subject_is_valid(Channel.USB, "mount", {**good, "device": "usb"}) is False
    assert subject_is_valid(Channel.USB, "mount", without_volume) is False


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

    body = "\n".join([event(subject={"volume": VOLUME}), event()]).encode()
    parsed = parse_batch(body, 10)

    assert [(r.line, r.reason) for r in parsed.rejected] == [(1, "invalid_event")]
    assert [line for line, _ in parsed.events] == [2]
