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


class ConfigTreeError(BarysGuardError):
    """Дерево групп не даёт построить цепочку наследования.

    Возникает при цикле parent_id либо при чрезмерной глубине. Дерево строят
    операторы, а ON DELETE SET NULL не исключает цикл полностью; бесконечный
    обход в обработчике запроса недопустим.
    """
