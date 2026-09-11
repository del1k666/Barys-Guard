from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.config import Settings, get_settings
from barysguard.core.errors import EnrollmentError, InvalidCsr
from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.db.session import get_session
from barysguard.gateway.deps import SERIAL_HEADER, current_agent
from barysguard.gateway.schemas import (
    EnrollRequest,
    EnrollResponse,
    RenewRequest,
    RenewResponse,
)
from barysguard.pki.ca import CertificateAuthority
from barysguard.pki.provider import get_ca
from barysguard.pki.service import issue_certificate, supersede_certificate
from barysguard.services.enrollment import consume_enrollment_token

router = APIRouter(prefix="/gateway/v1", tags=["gateway"])


@router.get("/ca", response_class=Response)
async def get_ca_certificate(ca: CertificateAuthority = Depends(get_ca)) -> Response:
    """Сертификат удостоверяющего центра. Без аутентификации — он публичен по определению."""
    return Response(content=ca.certificate_pem, media_type="application/x-pem-file")


@router.post("/enroll", response_model=EnrollResponse, status_code=status.HTTP_201_CREATED)
async def enroll(
    payload: EnrollRequest,
    session: AsyncSession = Depends(get_session),
    ca: CertificateAuthority = Depends(get_ca),
    settings: Settings = Depends(get_settings),
) -> EnrollResponse:
    try:
        token = await consume_enrollment_token(session, payload.token)
    except EnrollmentError as exc:
        # Все причины отказа отдаются одинаково: различие в ответах
        # позволило бы перебором отличать существующий токен от несуществующего.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "enrollment refused") from exc

    existing = await session.execute(
        select(Agent).where(Agent.machine_id == payload.host.machine_id).with_for_update()
    )
    agent = existing.scalar_one_or_none()

    if agent is None:
        agent = Agent(
            machine_id=payload.host.machine_id,
            hostname=payload.host.hostname,
            os=payload.host.os,
            os_version=payload.host.os_version,
            arch=payload.host.arch,
            agent_version=payload.host.agent_version,
            group_id=token.group_id,
            status=AgentStatus.ACTIVE,
        )
        session.add(agent)
    else:
        # Повторная регистрация того же железа обновляет факты, но не плодит агентов.
        agent.hostname = payload.host.hostname
        agent.os_version = payload.host.os_version
        agent.agent_version = payload.host.agent_version
        agent.status = AgentStatus.ACTIVE

    await session.flush()

    try:
        certificate_pem, _ = await issue_certificate(
            session, ca, agent, payload.csr_pem.encode("utf-8"), settings.agent_cert_days
        )
    except InvalidCsr as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid CSR") from exc

    return EnrollResponse(
        agent_id=agent.id,
        certificate_pem=certificate_pem.decode("ascii"),
        ca_pem=ca.certificate_pem.decode("ascii"),
        config_version=agent.config_version,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
    )


@router.get("/whoami")
async def whoami(agent: Agent = Depends(current_agent)) -> dict[str, str]:
    """Проверка аутентификации по клиентскому сертификату."""
    return {"agent_id": str(agent.id), "hostname": agent.hostname}


@router.post("/renew", response_model=RenewResponse)
async def renew(
    payload: RenewRequest,
    request: Request,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
    ca: CertificateAuthority = Depends(get_ca),
    settings: Settings = Depends(get_settings),
) -> RenewResponse:
    try:
        certificate_pem, record = await issue_certificate(
            session, ca, agent, payload.csr_pem.encode("utf-8"), settings.agent_cert_days
        )
    except InvalidCsr as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid CSR") from exc

    old_serial = request.headers.get(SERIAL_HEADER, "")
    if old_serial:
        await supersede_certificate(session, old_serial, record.id)

    return RenewResponse(
        certificate_pem=certificate_pem.decode("ascii"),
        ca_pem=ca.certificate_pem.decode("ascii"),
        not_after=record.not_after,
    )
