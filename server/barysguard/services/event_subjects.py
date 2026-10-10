"""Проверка subject событий каналов file и usb.

Схема subject определяется каналом (раздел 9 основной спеки). Проверяется
только то, без чего событие бесполезно оператору или опасно для разбора;
лишние поля допускаются, чтобы агент новой версии не отбрасывался.
"""

from typing import Any

from barysguard.gateway.event_schemas import Channel

FILE_ACTIONS = frozenset({"create", "modify", "rename", "delete", "copy"})
USB_ACTIONS = frozenset({"mount", "unmount"})
NETWORK_UPLOAD_ACTION = "upload"
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


def subject_is_valid(channel: Channel, action: str, subject: dict[str, Any]) -> bool:
    if channel is Channel.FILE:
        return _file_ok(action, subject)
    if channel is Channel.USB:
        return _usb_ok(action, subject)
    if channel is Channel.NETWORK:
        return _network_ok(action, subject)
    return True
