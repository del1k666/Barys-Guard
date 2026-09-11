from functools import lru_cache

from barysguard.core.config import get_settings
from barysguard.pki.ca import CertificateAuthority, ensure_ca


@lru_cache
def get_ca() -> CertificateAuthority:
    """Удостоверяющий центр создаётся при первом обращении и далее кешируется.

    Кеш сбрасывается вызовом get_ca.cache_clear() — используется в тестах.
    """
    settings = get_settings()
    return ensure_ca(
        settings.ca_dir,
        settings.ca_passphrase,
        settings.ca_common_name,
        settings.ca_valid_days,
    )
