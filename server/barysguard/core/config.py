from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки сервера. Все переменные окружения имеют префикс BG_."""

    model_config = SettingsConfigDict(
        env_prefix="BG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Подключение к базе данных
    database_url: str = "postgresql+asyncpg://barysguard:barysguard@localhost:5432/barysguard"

    # Сетевые параметры
    listen_host: str = "127.0.0.1"
    listen_port: int = 8000

    # Журналирование
    log_level: str = "INFO"

    # Удостоверяющий центр
    ca_dir: Path = Path("/var/lib/barysguard/pki")
    ca_passphrase: str = ""
    ca_common_name: str = "BarysGuard Internal CA"
    ca_valid_days: int = 3650

    # Сертификаты агентов
    agent_cert_days: int = 90
    agent_cert_renew_after_days: int = 60

    # Регистрация агентов
    enrollment_token_ttl_hours: int = 24

    # Опрос команд агентом
    heartbeat_interval_seconds: int = 30

    # Сессии веб-консоли
    session_cookie_name: str = "bg_session"
    session_ttl_minutes: int = 720
    session_idle_minutes: int = 30

    # Снимается только для локального стенда по http: без флага Secure
    # браузер отправит cookie сессии в открытом виде.
    cookie_secure: bool = True

    # Источники, которым разрешено изменять состояние по cookie-сессии.
    # Пусто — разрешён только собственный адрес запроса; для разработки
    # сюда добавляется адрес Vite: "http://localhost:5173".
    console_origins: str = ""

    @property
    def console_origin_list(self) -> list[str]:
        return [
            item.strip().rstrip("/") for item in self.console_origins.split(",") if item.strip()
        ]

    # Только для dev-стенда (deploy/stand). Без BG_STAND=1 команда
    # bootstrap-dev не работает: фиксированный пароль администратора не должен
    # появиться на боевом сервере по недосмотру.
    stand: bool = False
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = ""
    stand_tls_dir: Path = Path("/tls")
    stand_enroll_dir: Path = Path("/enroll")

    # Хранилище артефактов (используется в плане 1C)
    artifact_path: Path = Path("/var/lib/barysguard/artifacts")
    artifact_master_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
