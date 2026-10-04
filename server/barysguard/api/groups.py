"""Дерево групп агентов.

Группа определяет две вещи сразу: чью конфигурацию наследует агент и кому
он виден. Поэтому перенос и удаление здесь обставлены проверками плотнее,
чем обычный CRUD: ошибка в дереве — это либо хост без политик, либо
оператор, увидевший чужой филиал.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import current_user, require_admin
from barysguard.api.schemas import CreateGroupRequest, GroupSummary, UpdateGroupRequest
from barysguard.db.models.agent import Agent, AgentGroup
from barysguard.db.models.user import User
from barysguard.db.session import get_session
from barysguard.services.audit import record_audit
from barysguard.services.scope import scope_group_ids, subtree_group_ids

router = APIRouter(prefix="/api/v1/groups", tags=["api"])


async def _summary(session: AsyncSession, group: AgentGroup) -> GroupSummary:
    agent_count = (
        await session.execute(
            select(func.count()).select_from(Agent).where(Agent.group_id == group.id)
        )
    ).scalar_one()
    return GroupSummary(
        id=group.id,
        name=group.name,
        parent_id=group.parent_id,
        agent_count=agent_count,
        created_at=group.created_at,
    )


@router.get("", response_model=list[GroupSummary])
async def list_groups(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[GroupSummary]:
    statement = select(AgentGroup).order_by(AgentGroup.name)

    visible = await scope_group_ids(session, user)
    if visible is not None:
        statement = statement.where(AgentGroup.id.in_(visible))

    groups = (await session.execute(statement)).scalars().all()
    return [await _summary(session, group) for group in groups]


@router.post("", response_model=GroupSummary, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: CreateGroupRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> GroupSummary:
    if payload.parent_id is not None:
        parent = await session.get(AgentGroup, payload.parent_id)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "parent group not found")

    group = AgentGroup(name=payload.name, parent_id=payload.parent_id)
    session.add(group)
    await session.flush()

    await record_audit(
        session,
        user_id=user.id,
        action="group.create",
        target_type="agent_group",
        target_id=group.id,
        payload={
            "name": group.name,
            "parent_id": str(group.parent_id) if group.parent_id else None,
        },
    )

    return await _summary(session, group)


@router.patch("/{group_id}", response_model=GroupSummary)
async def update_group(
    group_id: uuid.UUID,
    payload: UpdateGroupRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> GroupSummary:
    group = await session.get(AgentGroup, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found")

    fields = payload.model_fields_set
    changes: dict[str, str | None] = {}

    if "name" in fields and payload.name is not None:
        group.name = payload.name
        changes["name"] = payload.name

    if "parent_id" in fields:
        if payload.parent_id is not None:
            parent = await session.get(AgentGroup, payload.parent_id)
            if parent is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "parent group not found")

            # Родителем не может стать ни сама группа, ни её потомок: цикл
            # в дереве зациклил бы и сбор конфигурации, и обход области
            # видимости — обе операции идут по родителям до корня.
            if payload.parent_id == group.id or payload.parent_id in await subtree_group_ids(
                session, group.id
            ):
                raise HTTPException(status.HTTP_409_CONFLICT, "group cannot descend from itself")

        group.parent_id = payload.parent_id
        changes["parent_id"] = str(payload.parent_id) if payload.parent_id else None

    await session.flush()

    if changes:
        await record_audit(
            session,
            user_id=user.id,
            action="group.update",
            target_type="agent_group",
            target_id=group.id,
            payload=changes,
        )

    return await _summary(session, group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    group = await session.get(AgentGroup, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "group not found")

    children = (
        await session.execute(
            select(func.count()).select_from(AgentGroup).where(AgentGroup.parent_id == group.id)
        )
    ).scalar_one()
    agents = (
        await session.execute(
            select(func.count()).select_from(Agent).where(Agent.group_id == group.id)
        )
    ).scalar_one()
    operators = (
        await session.execute(
            select(func.count()).select_from(User).where(User.scope_group_id == group.id)
        )
    ).scalar_one()

    # Каскадного удаления нет намеренно. Агенты остались бы без политик,
    # подгруппы — без родителя, а операторы филиала внезапно увидели бы
    # весь флот. Пусть администратор разберёт группу руками.
    if children or agents or operators:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"group is not empty: {children} subgroups, {agents} agents, {operators} operators",
        )

    # Имя снимается до удаления: после него обращение к атрибутам
    # удалённого объекта уже ничего не значит.
    name = group.name
    await session.delete(group)
    await session.flush()

    await record_audit(
        session,
        user_id=user.id,
        action="group.delete",
        target_type="agent_group",
        target_id=group_id,
        payload={"name": name},
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
