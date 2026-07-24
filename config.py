import json
import os
from pathlib import Path


class Config:
    """Простое хранилище настроек в JSON файле рядом с приложением."""
    _path = None
    _data = {}

    DEFAULTS = {
        "vt_api_key":        "",
        "abuseipdb_key":     "",
        "groq_key":          "",
        "claude_key":        "",
        "ai_provider":       "groq",
        "results_dir":       "C:/Tools/results",
        "quarantine_dir":    "C:/Tools/quarantine",
        "vt_rate_limit_sec": 15,
        "auto_save_reports": False,
        "theme":             "dark",
        "language":          "rus",
    }

    @classmethod
    def init(cls):
        try:
            cls._path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        except NameError:
            cls._path = os.path.abspath("config.json")
        cls.load()

    @classmethod
    def load(cls):
        cls._data = dict(cls.DEFAULTS)
        if cls._path and os.path.exists(cls._path):
            try:
                with open(cls._path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                cls._data.update(saved)
            except Exception:
                pass

    @classmethod
    def save(cls):
        try:
            with open(cls._path, "w", encoding="utf-8") as f:
                json.dump(cls._data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    @classmethod
    def get(cls, key, default=None):
        return cls._data.get(key, default if default is not None else cls.DEFAULTS.get(key))

    @classmethod
    def set(cls, key, value):
        cls._data[key] = value
        cls.save()

    @classmethod
    def reset(cls):
        cls._data = dict(cls.DEFAULTS)
        cls.save()


Config.init()

def _get_vt_key():    return Config.get("vt_api_key")
def _get_abuse_key(): return Config.get("abuseipdb_key")
def _get_groq_key():  return Config.get("groq_key")
def _get_claude_key():return Config.get("claude_key")

VT_URL         = "https://www.virustotal.com/api/v3/files/{}"

def get_results_dir() -> Path:
    return Path(Config.get("results_dir", "C:/Tools/results"))

def get_quarantine_dir() -> Path:
    return Path(Config.get("quarantine_dir", "C:/Tools/quarantine"))
