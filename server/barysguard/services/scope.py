"""Область видимости оператора.

Одна функция на всю систему: любое место, где оператор видит агентов,
команды, токены или конфигурацию, спрашивает область видимости здесь.
Разведи это правило по нескольким обработчикам — и рано или поздно один
из них разойдётся с остальными, а расхождение в DLP означает, что офицер
одного филиала прочитал перехват другого.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.db.models.agent import AgentGroup
from barysguard.db.models.user import User


async def subtree_group_ids(db: AsyncSession, root_id: uuid.UUID) -> set[uuid.UUID]:
    """Группа и все её потомки.

    Обход выполняет база рекурсивным запросом: дерево филиалов меняется
    редко, но читается на каждый запрос списка агентов, и вытягивать его
    целиком в приложение ради пересчёта в Python значило бы платить
    обходом всего дерева за каждую страницу таблицы.
    """
    anchor = (
        select(AgentGroup.id, AgentGroup.parent_id)
        .where(AgentGroup.id == root_id)
        .cte("scope", recursive=True)
    )
    child = select(AgentGroup.id, AgentGroup.parent_id).join(
        anchor, AgentGroup.parent_id == anchor.c.id
    )
    tree = anchor.union_all(child)

    rows = (await db.execute(select(tree.c.id))).scalars().all()
    return set(rows)


async def scope_group_ids(db: AsyncSession, user: User) -> set[uuid.UUID] | None:
    """Группы, видимые оператору. None означает «весь флот, без ограничения».

    Пустое множество и None различаются намеренно: первое — оператор,
    чья группа удалена, и он не должен увидеть ничего; второе — оператор
    без ограничения области. Свести их к одному значению значит однажды
    показать уволенному филиалу весь флот.
    """
    if user.scope_group_id is None:
        return None

    return await subtree_group_ids(db, user.scope_group_id)
