import argparse
import asyncio
import uuid

from barysguard.core.config import get_settings
from barysguard.db.models.user import UserRole
from barysguard.db.session import create_engine_from_url, session_factory
from barysguard.devstand import DEFAULT_GROUPS, StandOptions, bootstrap_stand
from barysguard.pki.provider import get_ca
from barysguard.services.demo_events import seed_demo_events
from barysguard.services.event_partitions import ensure_event_partitions
from barysguard.services.inspection.rules import seed_rules
from barysguard.services.users import create_account, username_taken


async def _create_user(
    username: str,
    role: str,
    with_api_key: bool,
    scope_group_id: str | None,
) -> int:
    engine = create_engine_from_url(get_settings().database_url)

    try:
        async with session_factory(engine)() as session:
            if await username_taken(session, username):
                print(f"оператор {username} уже существует")
                return 1

            user, password, api_key = await create_account(
                session,
                username=username,
                role=UserRole(role),
                scope_group_id=uuid.UUID(scope_group_id) if scope_group_id else None,
                # Ключ вместо пароля: машине неоткуда вводить пароль,
                # человеку не нужен ключ.
                with_password=not with_api_key,
                with_api_key=with_api_key,
            )
            await session.commit()

            print(f"оператор: {user.username}")
            print(f"роль:     {user.role.value}")
            if password:
                print(f"пароль:   {password}")
                print("Пароль временный: система потребует сменить его при первом входе.")
            if api_key:
                print(f"ключ:     {api_key}")
            print("Секрет показывается один раз. Сохраните его сейчас.")
    finally:
        await engine.dispose()

    return 0


async def _bootstrap_dev() -> int:
    settings = get_settings()

    if not settings.stand:
        print("bootstrap-dev работает только на dev-стенде: задайте BG_STAND=1.")
        return 1
    if not settings.bootstrap_admin_password:
        print("Не задан BG_BOOTSTRAP_ADMIN_PASSWORD: пароль администратора стенда неизвестен.")
        return 1

    options = StandOptions(
        admin_username=settings.bootstrap_admin_username,
        admin_password=settings.bootstrap_admin_password,
        tls_dir=settings.stand_tls_dir,
        enroll_dir=settings.stand_enroll_dir,
        groups=DEFAULT_GROUPS,
    )

    engine = create_engine_from_url(settings.database_url)
    try:
        async with session_factory(engine)() as session:
            report = await bootstrap_stand(session, get_ca(), options)
            await seed_rules(session)
            await session.commit()
    finally:
        await engine.dispose()

    admin_state = "создан" if report.admin_created else "уже был"
    print(f"администратор: {options.admin_username} ({admin_state})")
    print(
        f"группы создано: {len(report.groups_created)}, "
        f"токенов выдано: {len(report.tokens_created)}"
    )
    print(f"серверный сертификат: {'выпущен' if report.certificate_issued else 'действующий'}")
    return 0


async def _ensure_partitions(months_ahead: int) -> int:
    engine = create_engine_from_url(get_settings().database_url)
    try:
        async with session_factory(engine)() as session:
            created = await ensure_event_partitions(session, months_ahead)
            await session.commit()
    finally:
        await engine.dispose()

    print(f"создано разделов: {len(created)}")
    for name in created:
        print(f"  {name}")
    return 0


async def _seed_demo_events() -> int:
    settings = get_settings()

    if not settings.stand:
        print("seed-demo-events работает только на dev-стенде: задайте BG_STAND=1.")
        return 1

    engine = create_engine_from_url(settings.database_url)
    try:
        async with session_factory(engine)() as session:
            result = await seed_demo_events(session)
            await session.commit()
    finally:
        await engine.dispose()

    print(f"агентов: {result.agents}, добавлено событий: {result.inserted}")
    return 0


async def _seed_rules() -> int:
    engine = create_engine_from_url(get_settings().database_url)
    try:
        async with session_factory(engine)() as session:
            report = await seed_rules(session)
            await session.commit()
    finally:
        await engine.dispose()

    print(
        f"правил создано: {report.rules_created}, версий: {report.versions_created}, "
        f"терминов добавлено: {report.terms_added}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="barysguard-admin")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="создать оператора")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=[r.value for r in UserRole], default="operator")
    create.add_argument(
        "--api-key",
        action="store_true",
        help="учётная запись для автоматизации: выдать ключ вместо пароля",
    )
    create.add_argument("--scope-group-id", default=None, help="ограничить видимость поддеревом")

    sub.add_parser("bootstrap-dev", help="начальное состояние dev-стенда (нужен BG_STAND=1)")

    sub.add_parser(
        "seed-demo-events", help="демонстрационные события file/usb для стенда (нужен BG_STAND=1)"
    )

    sub.add_parser("seed-rules", help="завести встроенные правила инспекции (идемпотентно)")

    partitions = sub.add_parser("ensure-partitions", help="создать разделы таблицы events")
    partitions.add_argument("--months-ahead", type=int, default=2)

    args = parser.parse_args()
    if args.command == "create-user":
        code = asyncio.run(
            _create_user(args.username, args.role, args.api_key, args.scope_group_id)
        )
        raise SystemExit(code)
    if args.command == "bootstrap-dev":
        raise SystemExit(asyncio.run(_bootstrap_dev()))
    if args.command == "seed-demo-events":
        raise SystemExit(asyncio.run(_seed_demo_events()))
    if args.command == "seed-rules":
        raise SystemExit(asyncio.run(_seed_rules()))
    if args.command == "ensure-partitions":
        raise SystemExit(asyncio.run(_ensure_partitions(args.months_ahead)))
