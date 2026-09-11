import argparse
import asyncio

from barysguard.core.config import get_settings
from barysguard.db.models.user import UserRole
from barysguard.db.session import create_engine_from_url, session_factory
from barysguard.services.users import create_user


async def _create_user(username: str, role: str) -> None:
    engine = create_engine_from_url(get_settings().database_url)
    async with session_factory(engine)() as session:
        raw_key, user = await create_user(session, username=username, role=UserRole(role))
        await session.commit()

    print(f"user:    {user.username}")
    print(f"role:    {user.role.value}")
    print(f"api key: {raw_key}")
    print("Ключ показывается один раз. Сохраните его сейчас.")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="barysguard-admin")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="создать оператора")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=[r.value for r in UserRole], default="operator")

    args = parser.parse_args()
    if args.command == "create-user":
        asyncio.run(_create_user(args.username, args.role))
