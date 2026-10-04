import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.config import Settings, get_settings
from barysguard.db.models.agent import Agent
from barysguard.db.models.console_session import ConsoleSession
from barysguard.db.models.user import User, UserRole
from barysguard.db.session import get_session
from barysguard.services.auth import find_valid_session, touch_session
from barysguard.services.scope import scope_group_ids
from barysguard.services.users import find_active_user_by_key

API_KEY_HEADER = "X-Api-Key"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Principal:
    """Кто выполняет запрос и по какой форме доступа.

    Сессия присутствует только у браузера. Автоматизация ходит по API-ключу,
    и отзывать ей нечего: ключ отзывается вместе с учётной записью.
    """

    user: User
    session: ConsoleSession | None


def _allowed_origins(settings: Settings, request: Request) -> set[str]:
    configured = settings.console_origin_list
    if configured:
        return set(configured)

    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host")
    return {f"{scheme}://{host}"} if host else set()


def _reject_foreign_origin(settings: Settings, request: Request) -> None:
    """Защита от CSRF для доступа по cookie.

    Проверяется присланный Origin, а его отсутствие пропускается: заголовок
    ставит браузер, и межсайтовый запрос из браузера без него не обходится.
    Отсутствие Origin означает не-браузерного клиента, для которого CSRF
    неосуществим по построению: у него нет чужой вкладки с нашей cookie.
    """
    if request.method not in UNSAFE_METHODS:
        return

    origin = request.headers.get("origin")
    if origin is None:
        return

    if origin.rstrip("/") not in _allowed_origins(settings, request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "origin not allowed")


async def current_principal(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Principal:
    """Личность оператора: cookie сессии либо API-ключ.

    Cookie проверяется первой. Браузер присылает её на каждый запрос,
    и если она есть, то именно она и выражает намерение пользователя.
    """
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        record = await find_valid_session(
            session, raw_token, idle_minutes=settings.session_idle_minutes
        )
        if record is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired")

        user = (
            await session.execute(select(User).where(User.id == record.user_id))
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired")

        _reject_foreign_origin(settings, request)
        await touch_session(session, record)
        return Principal(user=user, session=record)

    raw_key = request.headers.get(API_KEY_HEADER)
    if not raw_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")

    user = await find_active_user_by_key(session, raw_key)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")

    return Principal(user=user, session=None)


async def current_user(principal: Principal = Depends(current_principal)) -> User:
    return principal.user


def require_role(*roles: UserRole) -> Callable[..., Coroutine[Any, Any, User]]:
    async def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return user

    return dependency


# Фабрика зависимостей вычисляется один раз на модуль, а не на каждый
# разбор сигнатуры обработчика: вызов в значении по умолчанию — это то,
# от чего предостерегает B008.
require_admin = require_role(UserRole.ADMIN)


async def agent_in_scope(session: AsyncSession, user: User, agent_id: uuid.UUID) -> Agent | None:
    """Агент, видимый этому оператору. None означает «нет либо не виден».

    Отсутствие и невидимость отдаются одинаково: иначе перебор
    идентификаторов раскрыл бы состав чужого филиала.
    """
    agent = (await session.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        return None

    visible = await scope_group_ids(session, user)
    if visible is None:
        return agent

    # Агент без группы виден только оператору без ограничения области:
    # нераспределённый хост не принадлежит ни одному филиалу.
    if agent.group_id is None or agent.group_id not in visible:
        return None

    return agent
