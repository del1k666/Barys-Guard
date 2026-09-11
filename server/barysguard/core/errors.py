class BarysGuardError(Exception):
    """Базовое исключение домена."""


class EnrollmentError(BarysGuardError):
    """Регистрация агента невозможна."""


class TokenNotFound(EnrollmentError):
    pass


class TokenExpired(EnrollmentError):
    pass


class TokenExhausted(EnrollmentError):
    pass


class TokenRevoked(EnrollmentError):
    pass


class PkiError(BarysGuardError):
    """Ошибка удостоверяющего центра."""


class InvalidCsr(PkiError):
    pass
