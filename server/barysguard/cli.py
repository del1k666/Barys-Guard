import argparse
import asyncio
import uuid

from barysguard.core.config import get_settings
from barysguard.db.models.user import UserRole
from barysguard.db.session import create_engine_from_url, session_factory
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

    args = parser.parse_args()
    if args.command == "create-user":
        code = asyncio.run(
            _create_user(args.username, args.role, args.api_key, args.scope_group_id)
        )
        raise SystemExit(code)
