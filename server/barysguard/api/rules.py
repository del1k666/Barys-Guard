"""Правила и словари инспекции: только администратор."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from barysguard.api.deps import require_admin
from barysguard.api.schemas import (
    RuleCreateRequest,
    RuleMatchSpan,
    RuleSummary,
    RuleTestRequest,
    RuleTestResponse,
    RuleUpdateRequest,
    RuleVersionItem,
    TermItem,
    TermPage,
    TermsAddRequest,
    TermsAddResponse,
)
from barysguard.core.config import Settings, get_settings
from barysguard.db.models.user import User
from barysguard.db.session import get_session
from barysguard.services.audit import record_audit
from barysguard.services.inspection import rule_admin
from barysguard.services.inspection.rule_admin import RuleError, RuleView

router = APIRouter(prefix="/api/v1", tags=["rules"])


def _summary(view: RuleView) -> RuleSummary:
    return RuleSummary(**view.__dict__)


def _fail(error: RuleError) -> HTTPException:
    return HTTPException(error.status_code, error.message)


@router.get("/rules", response_model=list[RuleSummary])
async def list_rules(
    _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> list[RuleSummary]:
    return [_summary(v) for v in await rule_admin.list_rules(session)]


@router.post("/rules/test", response_model=RuleTestResponse)
async def run_rule_test(
    payload: RuleTestRequest,
    _: User = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> RuleTestResponse:
    """Проверка на тексте оператора; текст не сохраняется и в логи не попадает."""
    try:
        outcome = rule_admin.run_rule_test(
            payload.kind,
            pattern=payload.pattern,
            ignore_case=payload.ignore_case,
            terms=payload.terms,
            text=payload.text,
            max_pattern=settings.regex_max_pattern,
            max_match=settings.regex_max_match,
            max_text=settings.rules_test_max_text,
        )
    except RuleError as error:
        raise _fail(error) from None
    return RuleTestResponse(
        ok=outcome.ok,
        error=outcome.error,
        count=outcome.count,
        matches=[RuleMatchSpan(start=s, end=e) for s, e in outcome.matches],
    )


@router.post("/rules", response_model=RuleSummary, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: RuleCreateRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RuleSummary:
    try:
        view = await rule_admin.create_rule(
            session,
            settings,
            kind=payload.kind,
            title=payload.title,
            weight=payload.weight,
            cap=payload.cap,
            pattern=payload.pattern,
            ignore_case=payload.ignore_case,
            test_text=payload.test_text,
            terms=payload.terms,
        )
    except RuleError as error:
        raise _fail(error) from None
    audit: dict[str, Any] = {
        "key": view.key,
        "kind": view.kind,
        "title": view.title,
        "weight": view.weight,
        "cap": view.cap,
    }
    if view.pattern is not None:
        audit["pattern"] = view.pattern
    await record_audit(
        session,
        user_id=user.id,
        action="rule.create",
        target_type="rule",
        target_id=view.id,
        payload=audit,
    )
    return _summary(view)


@router.get("/rules/{rule_id}", response_model=RuleSummary)
async def read_rule(
    rule_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> RuleSummary:
    try:
        return _summary(await rule_admin.get_rule(session, rule_id))
    except RuleError as error:
        raise _fail(error) from None


@router.patch("/rules/{rule_id}", response_model=RuleSummary)
async def update_rule(
    rule_id: uuid.UUID,
    payload: RuleUpdateRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RuleSummary:
    try:
        view, changes = await rule_admin.update_rule(
            session, settings, rule_id, payload.model_dump(exclude_unset=True)
        )
    except RuleError as error:
        raise _fail(error) from None
    if changes:
        await record_audit(
            session,
            user_id=user.id,
            action="rule.update",
            target_type="rule",
            target_id=view.id,
            payload={"key": view.key, "changes": changes},
        )
    return _summary(view)


@router.get("/rules/{rule_id}/versions", response_model=list[RuleVersionItem])
async def rule_versions(
    rule_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[RuleVersionItem]:
    try:
        versions = await rule_admin.list_versions(session, rule_id)
    except RuleError as error:
        raise _fail(error) from None
    return [
        RuleVersionItem(version=v.version, params=v.params, created_at=v.created_at)
        for v in versions
    ]


@router.get("/rules/{rule_id}/terms", response_model=TermPage)
async def rule_terms(
    rule_id: uuid.UUID,
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> TermPage:
    try:
        items, total = await rule_admin.list_terms(session, rule_id, q, limit, offset)
    except RuleError as error:
        raise _fail(error) from None
    return TermPage(items=[TermItem(id=t.id, term=t.term) for t in items], total=total)


@router.post("/rules/{rule_id}/terms", response_model=TermsAddResponse)
async def add_rule_terms(
    rule_id: uuid.UUID,
    payload: TermsAddRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> TermsAddResponse:
    try:
        added = await rule_admin.add_terms(session, rule_id, payload.terms)
    except RuleError as error:
        raise _fail(error) from None
    if added:
        await record_audit(
            session,
            user_id=user.id,
            action="rule.terms_add",
            target_type="rule",
            target_id=rule_id,
            payload={"added": added},
        )
    return TermsAddResponse(added=added)


@router.delete("/rules/{rule_id}/terms/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule_term(
    rule_id: uuid.UUID,
    term_id: uuid.UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        await rule_admin.delete_term(session, rule_id, term_id)
    except RuleError as error:
        raise _fail(error) from None
    await record_audit(
        session,
        user_id=user.id,
        action="rule.term_delete",
        target_type="rule",
        target_id=rule_id,
        payload={"term_id": str(term_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
