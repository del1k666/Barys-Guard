import ipaddress
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.core.config import Settings, get_settings
from barysguard.core.errors import CommandNotDelivered, EnrollmentError, InvalidCsr
from barysguard.db.models.agent import Agent, AgentStatus
from barysguard.db.models.command import CommandStatus
from barysguard.db.session import get_session
from barysguard.gateway.deps import CLIENT_IP_HEADER, SERIAL_HEADER, current_agent
from barysguard.gateway.schemas import (
    AgentConfigResponse,
    CommandResultRequest,
    EnrollRequest,
    EnrollResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    QueuedCommand,
    RenewRequest,
    RenewResponse,
)
from barysguard.pki.ca import CertificateAuthority
from barysguard.pki.provider import get_ca
from barysguard.pki.service import issue_certificate, supersede_certificate
from barysguard.services.commands import (
    MAX_COMMANDS_PER_HEARTBEAT,
    MAX_RESULT_BYTES,
    dequeue_commands,
    expire_stale_commands,
    record_command_result,
)
from barysguard.services.config import effective_config_for_agent
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

    # Агент, получивший версию 0, обнаружил бы расхождение на первом же
    # heartbeat и сходил за конфигом лишний раз. При массовом развёртывании
    # это заметная лишняя волна.
    document, config_version = await effective_config_for_agent(session, agent)

    return EnrollResponse(
        agent_id=agent.id,
        certificate_pem=certificate_pem.decode("ascii"),
        ca_pem=ca.certificate_pem.decode("ascii"),
        config_version=config_version,
        heartbeat_interval_seconds=document["transport"]["heartbeat_interval_seconds"],
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


@router.get("/config", response_model=AgentConfigResponse)
async def get_agent_config(
    request: Request,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
) -> Response:
    document, version = await effective_config_for_agent(session, agent)
    etag = f'"{version}"'

    # Агенты опрашивают конфиг редко, но после перезапуска сервера делают это
    # одновременно. Пустой ответ на совпавшую версию дешевле полного документа.
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    return JSONResponse(
        content={"version": version, "document": document},
        headers={"ETag": etag},
    )


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    payload: HeartbeatRequest,
    request: Request,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
) -> HeartbeatResponse:
    now = datetime.now(UTC)

    # Часы агента могут прислать наивную отметку. Приводим к UTC явно:
    # иначе вычитание datetime бросит TypeError уже в боевой эксплуатации.
    sent_at = payload.sent_at
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)

    agent.last_heartbeat_at = now
    agent.agent_version = payload.agent_version
    agent.config_version = payload.config_version
    agent.clock_skew_ms = int((sent_at - now).total_seconds() * 1000)

    client_ip = request.headers.get(CLIENT_IP_HEADER)
    if client_ip:
        try:
            # Колонка типа INET: мусор в заголовке иначе уронит транзакцию.
            ipaddress.ip_address(client_ip)
        except ValueError:
            pass
        else:
            agent.last_ip = client_ip

    # Карантин и отзыв снимает только оператор: heartbeat их не отменяет.
    if agent.status in (AgentStatus.PENDING, AgentStatus.OFFLINE):
        agent.status = AgentStatus.ACTIVE

    document, config_version = await effective_config_for_agent(session, agent)

    await expire_stale_commands(session, agent.id)
    commands = await dequeue_commands(session, agent.id, MAX_COMMANDS_PER_HEARTBEAT)

    return HeartbeatResponse(
        server_time=now,
        config_version=config_version,
        heartbeat_interval_seconds=document["transport"]["heartbeat_interval_seconds"],
        commands=[
            QueuedCommand(
                id=command.id,
                type=command.type.value,
                payload=command.payload,
                expires_at=command.expires_at,
            )
            for command in commands
        ],
    )


@router.post("/commands/{command_id}/result")
async def submit_command_result(
    command_id: uuid.UUID,
    payload: CommandResultRequest,
    response: Response,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    encoded = json.dumps(payload.result, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "command result too large")

    try:
        outcome = await record_command_result(
            session,
            agent_id=agent.id,
            command_id=command_id,
            status=CommandStatus(payload.status),
            result=payload.result,
        )
    except CommandNotDelivered as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "command was not delivered") from exc

    if outcome is None:
        # Не 403: различие в ответах позволило бы перебором идентификаторов
        # выяснять, какие команды существуют в системе.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "command not found")

    command, accepted = outcome
    # Повтор после обрыва связи — не ошибка, но и не новое принятие.
    response.status_code = status.HTTP_202_ACCEPTED if accepted else status.HTTP_200_OK
    return {"status": command.status.value}
