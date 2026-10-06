"""Заведение встроенных правил и загрузка действующего набора."""

from sqlalchemy import select

from barysguard.db.models.inspection import Dictionary, DictionaryTerm, Rule, RuleVersion
from barysguard.services.inspection.detectors import ContentScanner
from barysguard.services.inspection.rules import BUILTIN_TERMS, load_ruleset, seed_rules


async def test_seed_creates_three_rules_with_first_versions(app_client, session) -> None:
    report = await seed_rules(session)
    await session.commit()

    assert (report.rules_created, report.versions_created) == (3, 3)
    assert report.terms_added == len(BUILTIN_TERMS)
    keys = set((await session.scalars(select(Rule.key))).all())
    assert keys == {"iin_bin", "card", "markings"}
    versions = (await session.scalars(select(RuleVersion))).all()
    assert {v.version for v in versions} == {1}


async def test_seed_is_idempotent(app_client, session) -> None:
    await seed_rules(session)
    await session.commit()

    again = await seed_rules(session)
    await session.commit()

    assert (again.rules_created, again.versions_created, again.terms_added) == (0, 0, 0)
    assert len((await session.scalars(select(RuleVersion))).all()) == 3


async def test_changed_dictionary_gets_a_new_rule_version_and_ruleset_hash(
    app_client, session
) -> None:
    await seed_rules(session)
    await session.commit()
    before = await load_ruleset(session)

    dictionary = (await session.scalars(select(Dictionary))).one()
    session.add(DictionaryTerm(dictionary_id=dictionary.id, term="тайна"))
    await session.commit()
    report = await seed_rules(session)
    await session.commit()
    after = await load_ruleset(session)

    assert report.versions_created == 1
    assert after.hash != before.hash
    markings_versions = (
        await session.scalars(
            select(RuleVersion.version)
            .join(Rule)
            .where(Rule.key == "markings")
            .order_by(RuleVersion.version)
        )
    ).all()
    assert list(markings_versions) == [1, 2]


async def test_ruleset_builds_detectors_and_weights(app_client, session) -> None:
    await seed_rules(session)
    await session.commit()

    ruleset = await load_ruleset(session)

    weights = {w.key: (w.weight, w.cap) for w in ruleset.weights()}
    assert weights == {"iin_bin": (20, 5), "card": (25, 4), "markings": (15, 2)}
    assert set(ruleset.version_ids()) == {"iin_bin", "card", "markings"}

    scanner = ContentScanner(ruleset.detectors())
    scanner.feed("Строго конфиденциально. ИИН 900101300017, карта 4111 1111 1111 1111")
    found = scanner.finish()
    assert (found["iin_bin"].count, found["card"].count, found["markings"].count) == (1, 1, 1)


async def test_disabled_rule_leaves_the_ruleset(app_client, session) -> None:
    await seed_rules(session)
    await session.commit()
    full = await load_ruleset(session)
    rule = (await session.scalars(select(Rule).where(Rule.key == "card"))).one()
    rule.enabled = False
    await session.commit()

    reduced = await load_ruleset(session)

    assert {w.key for w in reduced.weights()} == {"iin_bin", "markings"}
    assert reduced.hash != full.hash


async def test_ruleset_is_empty_before_seeding(app_client, session) -> None:
    ruleset = await load_ruleset(session)

    assert ruleset.weights() == [] and ruleset.detectors() == []
