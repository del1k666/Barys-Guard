import json
import os
import uuid
import threading
from pathlib import Path

_lock: threading.Lock = threading.Lock()

_override = os.environ.get("HOSTS_FILE_OVERRIDE")
HOSTS_FILE = Path(_override) if _override else (
    Path(os.path.dirname(os.path.abspath(__file__))).parent / "hosts.json"
)


def load_hosts() -> list:
    with _lock:
        return _load_hosts_unsafe()


def _load_hosts_unsafe() -> list:
    if not HOSTS_FILE.exists():
        return []
    try:
        with open(HOSTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_hosts(hosts: list) -> None:
    with _lock:
        _save_hosts_unsafe(hosts)


def _save_hosts_unsafe(hosts: list) -> None:
    with open(HOSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(hosts, f, ensure_ascii=False, indent=2)


def add_host(name: str, ip: str, port: int, token: str) -> dict:
    entry = {
        "id":        str(uuid.uuid4()),
        "name":      name,
        "ip":        ip,
        "port":      port,
        "token":     token,
        "last_seen": None,
        "last_scan": None,
    }
    with _lock:
        hosts = _load_hosts_unsafe()
        hosts.append(entry)
        _save_hosts_unsafe(hosts)
    return entry


def update_host(host_id: str, **fields) -> None:
    with _lock:
        hosts = _load_hosts_unsafe()
        for h in hosts:
            if h["id"] == host_id:
                h.update(fields)
                break
        _save_hosts_unsafe(hosts)


def remove_host(host_id: str) -> None:
    with _lock:
        hosts = _load_hosts_unsafe()
        _save_hosts_unsafe([h for h in hosts if h["id"] != host_id])
