import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.agent import Agent
from barysguard.db.models.user import User, UserRole
from barysguard.db.session import get_session
from barysguard.services.users import find_active_user_by_key

API_KEY_HEADER = "X-Api-Key"


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    raw_key = request.headers.get(API_KEY_HEADER)
    if not raw_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "api key required")

    user = await find_active_user_by_key(session, raw_key)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")

    return user


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

    Сравнение плоское, а не по поддереву: ровно так область видимости уже
    применяется в списке агентов. Расширение до поддерева — подпроект 4,
    и делать его здесь в одном месте из двух значило бы развести поведение.
    """
    agent = (await session.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        return None
    if user.scope_group_id is not None and agent.group_id != user.scope_group_id:
        return None
    return agent
