"""Сервис управления правилами: валидация, версии, термины."""

import pytest
from sqlalchemy import select

from barysguard.core.config import Settings
from barysguard.db.models.inspection import RuleVersion
from barysguard.services.inspection.rule_admin import (
    RuleError,
    add_terms,
    create_rule,
    delete_term,
    get_rule,
    list_rules,
    list_terms,
    list_versions,
    run_rule_test,
    update_rule,
)
from barysguard.services.inspection.rules import load_ruleset, seed_rules

SETTINGS = Settings()
SAMPLE = "Договор №1234-567 подписан"


async def _seeded(session) -> None:
    await seed_rules(session)
    await session.commit()


async def _regex(session, **extra):
    args = {
        "kind": "regex",
        "title": "Номер договора",
        "weight": 30,
        "cap": 2,
        "pattern": r"№\s?\d{4}-\d{3}",
        "ignore_case": False,
        "test_text": SAMPLE,
        "terms": None,
    }
    args.update(extra)
    return await create_rule(session, SETTINGS, **args)


async def test_list_shows_the_three_builtin_rules(app_client, session) -> None:
    await _seeded(session)

    views = await list_rules(session)

    assert {v.key for v in views} == {"iin_bin", "card", "markings"}
    markings = next(v for v in views if v.key == "markings")
    assert markings.builtin and markings.terms_count == 7 and markings.version == 1
    assert next(v for v in views if v.key == "card").terms_count is None


async def test_create_regex_rule_needs_a_matching_test_text(app_client, session) -> None:
    await _seeded(session)

    view = await _regex(session)
    assert view.key.startswith("custom_") and not view.builtin and view.version == 1
    assert (view.pattern, view.weight, view.cap) == (r"№\s?\d{4}-\d{3}", 30, 2)

    with pytest.raises(RuleError) as no_text:
        await _regex(session, test_text=None)
    with pytest.raises(RuleError) as no_match:
        await _regex(session, test_text="здесь нет номера")
    assert (no_text.value.status_code, no_match.value.status_code) == (422, 422)


@pytest.mark.parametrize(
    "pattern", ["(?=a)b", "(", "a*", "x" * 501]
)  # lookahead, ошибка скобок, пустое совпадение, длина
async def test_bad_patterns_are_rejected(app_client, session, pattern: str) -> None:
    await _seeded(session)

    with pytest.raises(RuleError) as caught:
        await _regex(session, pattern=pattern, test_text="aaa")

    assert caught.value.status_code == 422 and caught.value.message


@pytest.mark.parametrize(
    "bad",
    [
        {"weight": 0},
        {"weight": 101},
        {"cap": 0},
        {"cap": 51},
        {"title": "  "},
        {"title": "x" * 121},
    ],
)
async def test_numbers_and_title_are_validated(app_client, session, bad: dict) -> None:
    await _seeded(session)

    with pytest.raises(RuleError) as caught:
        await _regex(session, **bad)

    assert caught.value.status_code == 422


async def test_create_dictionary_with_terms(app_client, session) -> None:
    await _seeded(session)

    view = await create_rule(
        session,
        SETTINGS,
        kind="dictionary",
        title="Проект Альфа",
        weight=20,
        cap=3,
        pattern=None,
        ignore_case=False,
        test_text=None,
        terms=["проект Альфа", "  Альфа-2  ", "проект   альфа", ""],
    )

    assert view.kind == "dictionary" and view.terms_count == 2
    ruleset = await load_ruleset(session)
    assert view.key in {w.key for w in ruleset.weights()}


async def test_update_publishes_a_version_only_when_parameters_change(app_client, session) -> None:
    await _seeded(session)
    card = next(v for v in await list_rules(session) if v.key == "card")

    renamed, _ = await update_rule(session, SETTINGS, card.id, {"title": "Карты"})
    assert renamed.version == 1 and renamed.title == "Карты"

    heavier, changes = await update_rule(session, SETTINGS, card.id, {"weight": 40})
    assert heavier.version == 2 and heavier.weight == 40
    assert changes == {"weight": [25, 40]}

    same, _ = await update_rule(session, SETTINGS, card.id, {"weight": 40})
    assert same.version == 2
    versions = await list_versions(session, card.id)
    assert [v.version for v in versions] == [2, 1]


async def test_toggle_does_not_create_a_version_but_changes_the_ruleset(
    app_client, session
) -> None:
    await _seeded(session)
    card = next(v for v in await list_rules(session) if v.key == "card")
    before = await load_ruleset(session)

    off, _ = await update_rule(session, SETTINGS, card.id, {"enabled": False})
    after = await load_ruleset(session)

    assert off.enabled is False and off.version == 1
    assert after.hash != before.hash and "card" not in {w.key for w in after.weights()}


async def test_the_last_enabled_rule_cannot_be_disabled(app_client, session) -> None:
    await _seeded(session)
    views = await list_rules(session)
    for view in views[:-1]:
        await update_rule(session, SETTINGS, view.id, {"enabled": False})

    with pytest.raises(RuleError) as caught:
        await update_rule(session, SETTINGS, views[-1].id, {"enabled": False})

    assert caught.value.status_code == 409


async def test_builtin_rules_keep_their_nature(app_client, session) -> None:
    await _seeded(session)
    card = next(v for v in await list_rules(session) if v.key == "card")

    with pytest.raises(RuleError) as caught:
        await update_rule(session, SETTINGS, card.id, {"pattern": "x", "test_text": "x"})

    assert caught.value.status_code == 422


async def test_changing_a_pattern_needs_a_matching_test_text(app_client, session) -> None:
    await _seeded(session)
    view = await _regex(session)

    with pytest.raises(RuleError):
        await update_rule(session, SETTINGS, view.id, {"pattern": "ALFA-\\d+"})
    changed, changes = await update_rule(
        session, SETTINGS, view.id, {"pattern": "ALFA-\\d+", "test_text": "код ALFA-123"}
    )

    assert changed.version == 2 and changed.pattern == "ALFA-\\d+"
    assert changes["pattern"] == [r"№\s?\d{4}-\d{3}", "ALFA-\\d+"]


async def test_terms_add_delete_and_search_publish_versions(app_client, session) -> None:
    await _seeded(session)
    markings = next(v for v in await list_rules(session) if v.key == "markings")

    added = await add_terms(session, markings.id, ["Только для своих", "секретно", "  "])
    assert added == 1  # «секретно» уже есть (без учёта регистра), пустая строка отброшена
    assert (await get_rule(session, markings.id)).version == 2

    items, total = await list_terms(session, markings.id, "своих", 50, 0)
    assert total == 1 and items[0].term == "Только для своих"

    await delete_term(session, markings.id, items[0].id)
    assert (await get_rule(session, markings.id)).version == 3
    assert (await list_terms(session, markings.id, "своих", 50, 0))[1] == 0


async def test_terms_limits_and_wrong_rule_kind(app_client, session) -> None:
    await _seeded(session)
    views = await list_rules(session)
    markings = next(v for v in views if v.key == "markings")
    card = next(v for v in views if v.key == "card")

    with pytest.raises(RuleError) as long_term:
        await add_terms(session, markings.id, ["я" * 201])
    with pytest.raises(RuleError) as too_many:
        await add_terms(session, markings.id, [f"t{i}" for i in range(501)])
    with pytest.raises(RuleError) as wrong_kind:
        await add_terms(session, card.id, ["x"])

    assert [e.value.status_code for e in (long_term, too_many, wrong_kind)] == [422, 422, 422]


async def test_unknown_rule_is_404(app_client, session) -> None:
    import uuid

    with pytest.raises(RuleError) as caught:
        await get_rule(session, uuid.uuid4())

    assert caught.value.status_code == 404


def test_test_rule_reports_positions_and_errors() -> None:
    ok = run_rule_test(
        "regex",
        pattern=r"\d{3}",
        ignore_case=False,
        terms=None,
        text="a 123 b 456",
        max_pattern=500,
        max_match=200,
        max_text=20000,
    )
    bad = run_rule_test(
        "regex",
        pattern="(?=a)b",
        ignore_case=False,
        terms=None,
        text="x",
        max_pattern=500,
        max_match=200,
        max_text=20000,
    )
    words = run_rule_test(
        "dictionary",
        pattern=None,
        ignore_case=False,
        terms=["секретно"],
        text="Это СЕКРЕТНО!",
        max_pattern=500,
        max_match=200,
        max_text=20000,
    )

    assert ok.ok and ok.count == 2 and ok.matches == [(2, 5), (8, 11)]
    assert not bad.ok and bad.error and bad.count == 0
    assert words.ok and words.count == 1 and words.matches == [(4, 12)]


async def test_concurrent_edits_do_not_lose_versions(
    app_client, session, migrated_database_url
) -> None:
    import asyncio

    from barysguard.db.session import create_engine_from_url, session_factory

    await _seeded(session)
    card = next(v for v in await list_rules(session) if v.key == "card")
    engine = create_engine_from_url(migrated_database_url)
    maker = session_factory(engine)

    async def edit(weight: int) -> None:
        async with maker() as other:
            await update_rule(other, SETTINGS, card.id, {"weight": weight})
            await other.commit()

    try:
        await asyncio.gather(edit(31), edit(32))
    finally:
        await engine.dispose()

    versions = (
        await session.scalars(select(RuleVersion.version).where(RuleVersion.rule_id == card.id))
    ).all()
    assert sorted(versions) == [1, 2, 3]


async def test_concurrent_disables_keep_one_rule_enabled(
    app_client, session, migrated_database_url
) -> None:
    import asyncio

    from barysguard.db.session import create_engine_from_url, session_factory

    await _seeded(session)
    views = await list_rules(session)
    for view in views[:-2]:
        await update_rule(session, SETTINGS, view.id, {"enabled": False})
    await session.commit()
    engine = create_engine_from_url(migrated_database_url)
    maker = session_factory(engine)

    async def disable(rule_id) -> object:
        async with maker() as other:
            try:
                await update_rule(other, SETTINGS, rule_id, {"enabled": False})
                await other.commit()
            except RuleError as exc:
                return exc.status_code
            return 200

    try:
        results = await asyncio.gather(disable(views[-2].id), disable(views[-1].id))
    finally:
        await engine.dispose()

    assert sorted(results) == [200, 409]


async def test_listing_survives_a_corrupt_pattern(app_client, session) -> None:
    await _seeded(session)
    view = await _regex(session)
    version = await session.scalar(select(RuleVersion).where(RuleVersion.rule_id == view.id))
    version.params = {**version.params, "pattern": "(?=broken"}
    await session.flush()

    assert view.id in {v.id for v in await list_rules(session)}


@pytest.mark.parametrize("name", ["title", "enabled", "weight", "cap", "pattern", "ignore_case"])
async def test_update_rejects_explicit_none(session, name) -> None:
    await _seeded(session)
    rule = next(r for r in await list_rules(session) if r.key == "card")

    with pytest.raises(RuleError) as caught:
        await update_rule(session, SETTINGS, rule.id, {name: None})

    assert caught.value.status_code == 422
