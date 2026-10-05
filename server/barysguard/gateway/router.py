import ipaddress
import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
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
    ArtifactChunkResponse,
    ArtifactOpenRequest,
    ArtifactOpenResponse,
    CommandResultRequest,
    EnrollRequest,
    EnrollResponse,
    EventsResult,
    HeartbeatRequest,
    HeartbeatResponse,
    QueuedCommand,
    RejectedLine,
    RenewRequest,
    RenewResponse,
)
from barysguard.pki.ca import CertificateAuthority
from barysguard.pki.provider import get_ca
from barysguard.pki.service import issue_certificate, supersede_certificate
from barysguard.services.artifacts import (
    ArtifactTooLarge,
    ChunkRejected,
    UploadNotFound,
    append_chunk,
    open_upload,
)
from barysguard.services.commands import (
    MAX_COMMANDS_PER_HEARTBEAT,
    MAX_RESULT_BYTES,
    dequeue_commands,
    expire_stale_commands,
    record_command_result,
)
from barysguard.services.config import effective_config_for_agent
from barysguard.services.enrollment import consume_enrollment_token
from barysguard.services.events import (
    BatchTooLargeError,
    BatchUnreadableError,
    parse_batch,
    store_events,
)
from barysguard.storage.artifact_store import FileArtifactStore, build_store

router = APIRouter(prefix="/gateway/v1", tags=["gateway"])
logger = logging.getLogger(__name__)


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


@router.post(
    "/events",
    response_model=EventsResult,
    status_code=status.HTTP_202_ACCEPTED,
    # Тело — NDJSON, FastAPI его не описывает: контракт задаётся вручную.
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/x-ndjson": {"schema": {"$ref": "#/components/schemas/EventEnvelope"}}
            },
        }
    },
)
async def ingest_events(
    request: Request,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
) -> EventsResult:
    """Пакет событий в NDJSON, одна строка — одно событие. Идемпотентно."""
    document, _ = await effective_config_for_agent(session, agent)
    max_events = document["transport"]["event_batch_max"]
    max_bytes = document["transport"]["event_batch_max_bytes"]

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "batch too large")

    # Тело читается потоком и обрывается на пределе: заголовок
    # Content-Length можно не прислать вовсе.
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "batch too large")
        chunks.append(chunk)

    try:
        parsed = parse_batch(b"".join(chunks), max_events)
    except BatchTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "batch too large") from exc
    except BatchUnreadableError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unreadable batch") from exc

    inserted = await store_events(
        session, agent.id, [envelope for _, envelope in parsed.events], datetime.now(UTC)
    )

    return EventsResult(
        accepted=inserted,
        duplicates=len(parsed.events) - inserted,
        rejected=[RejectedLine(line=r.line, reason=r.reason) for r in parsed.rejected],
    )


def _artifact_store(settings: Settings) -> FileArtifactStore:
    try:
        store = build_store(settings)
    except (ValueError, OSError):
        logger.error("мастер-ключ артефактов задан неверно", exc_info=True)
        store = None
    if store is None:
        # Остальной шлюз работает; агент считает это временной ошибкой.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "artifact storage not configured")
    return store


@router.post("/artifacts", response_model=ArtifactOpenResponse)
async def open_artifact_upload(
    payload: ArtifactOpenRequest,
    response: Response,
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ArtifactOpenResponse:
    """Открыть загрузку: сервер либо уже имеет артефакт, либо выдаёт сессию."""
    _artifact_store(settings)
    try:
        result = await open_upload(session, agent, payload.sha256, payload.size, settings)
    except ArtifactTooLarge as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "artifact too large") from exc

    if result.exists:
        response.status_code = status.HTTP_200_OK
        return ArtifactOpenResponse(status="exists")
    response.status_code = status.HTTP_201_CREATED
    return ArtifactOpenResponse(
        status="upload",
        upload_id=result.upload_id,
        received_bytes=result.received_bytes,
        chunk_size=settings.artifact_chunk_bytes,
    )


@router.put(
    "/artifacts/{upload_id}",
    response_model=ArtifactChunkResponse,
    # Тело — сырые байты, FastAPI его не описывает: контракт задаётся вручную.
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload_artifact_chunk(
    upload_id: uuid.UUID,
    request: Request,
    response: Response,
    x_offset: int = Header(alias="X-Offset", ge=0),
    agent: Agent = Depends(current_agent),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ArtifactChunkResponse | JSONResponse:
    """Очередной чанк. Смещение обязано совпасть с числом уже принятых байт."""
    store = _artifact_store(settings)
    limit = settings.artifact_chunk_bytes

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "chunk too large")

    parts: list[bytes] = []
    size = 0
    async for part in request.stream():
        size += len(part)
        if size > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "chunk too large")
        parts.append(part)

    try:
        result = await append_chunk(
            session, agent, upload_id, x_offset, b"".join(parts), settings, store
        )
    except UploadNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "upload not found") from exc
    except ChunkRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "chunk rejected") from exc

    if result.status == "offset_mismatch":
        return JSONResponse({"received_bytes": result.received_bytes}, status.HTTP_409_CONFLICT)
    if result.status == "hash_mismatch":
        return JSONResponse({"detail": "hash mismatch"}, status.HTTP_422_UNPROCESSABLE_CONTENT)

    complete = result.status == "complete"
    response.status_code = status.HTTP_201_CREATED if complete else status.HTTP_202_ACCEPTED
    return ArtifactChunkResponse(received_bytes=result.received_bytes, status=result.status)
