import threading

_lock: threading.Lock = threading.Lock()
_current: dict | None = None


def get_current_host() -> dict | None:
    with _lock:
        return _current


def set_current_host(host: dict | None) -> None:
    global _current
    with _lock:
        _current = host


def is_local() -> bool:
    with _lock:
        return _current is None
