from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.db.session import get_session
from barysguard.pki.service import find_active_certificate, normalize_serial

VERIFY_HEADER = "X-Client-Verify"
SERIAL_HEADER = "X-Client-Serial"
VERIFY_SUCCESS = "SUCCESS"

# Адрес из соединения для приложения всегда является адресом обратного прокси.
CLIENT_IP_HEADER = "X-Real-IP"


async def current_agent(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Agent:
    """Личность агента из клиентского сертификата.

    Заголовки проставляет nginx после успешной проверки сертификата.
    Конфигурация nginx ОБЯЗАНА вырезать эти заголовки, приходящие снаружи,
    иначе любой клиент объявит себя любым агентом (см. deploy/nginx/barysguard.conf).
    """
    if request.headers.get(VERIFY_HEADER) != VERIFY_SUCCESS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "client certificate not verified")

    raw_serial = request.headers.get(SERIAL_HEADER)
    if not raw_serial:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "client certificate not verified")

    certificate = await find_active_certificate(session, normalize_serial(raw_serial))
    if certificate is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "client certificate not verified")

    agent = (
        await session.execute(select(Agent).where(Agent.id == certificate.agent_id))
    ).scalar_one_or_none()

    if agent is None or agent.status == AgentStatus.REVOKED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "client certificate not verified")

    return agent
