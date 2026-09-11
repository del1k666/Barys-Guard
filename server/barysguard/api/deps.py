from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

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
