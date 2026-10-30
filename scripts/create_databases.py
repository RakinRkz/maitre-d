"""Create the application and test databases if they do not exist yet.

Run this after `docker compose up -d`, or on a fresh local Postgres, before the first `pytest`:
the suite recreates the schema inside `maitre_d_test` but cannot create the database itself.
Safe to run any number of times; an existing database is reported and left alone. Connection
strings are never printed, only database names.
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from maitre_d.config import Settings


async def create_database(url: str) -> None:
    parsed = make_url(url)
    name = parsed.database
    if not name:
        raise ValueError("the database URL has no database name")

    # Connect to the server's `postgres` maintenance database: CREATE DATABASE cannot run from
    # inside the database it creates.
    maintenance = parsed.set(database="postgres")
    engine = create_async_engine(maintenance, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if exists:
                print(f"{name}: already exists")
                return

            # Identifiers cannot be bound parameters, so quote it by hand. Doubling embedded
            # quotes is what Postgres itself expects.
            quoted = name.replace('"', '""')
            await connection.execute(text(f'CREATE DATABASE "{quoted}"'))
            print(f"{name}: created")
    finally:
        await engine.dispose()


async def main() -> int:
    settings = Settings()
    # The two URLs are normally different databases on one server; if `.env` points them at the
    # same one, create it once.
    urls = list(dict.fromkeys((settings.database_url, settings.test_database_url)))
    for url in urls:
        try:
            await create_database(url)
        except Exception as exc:
            # The exception text could contain the URL or password, so only its class is printed.
            print(
                "Could not create a database. Check DATABASE_URL and that the role has "
                f"CREATEDB permission ({type(exc).__name__})."
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
