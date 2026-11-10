"""Initialize the versioned local SQLite schema without running a session."""

from __future__ import annotations

import argparse
import asyncio
import os

from healteam_memory.resources import find_project_root
from healteam_memory.runtime_lease import RuntimeLease
from healteam_memory.training_store import SCHEMA_VERSION, TrainingRepository


async def migrate(database: str) -> None:
    repository = TrainingRepository(database)
    lease = RuntimeLease(repository.path)
    try:
        lease.acquire()
        await repository.initialize()
        print(f"Local training database is at schema version {SCHEMA_VERSION}")
    finally:
        try:
            await repository.dispose()
        finally:
            lease.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", help="SQLite file path or sqlite+aiosqlite URL")
    args = parser.parse_args()
    database = (
        args.database
        or os.getenv("HEALTEAM_DATABASE_URL")
        or str(find_project_root() / "data" / "healteam.sqlite3")
    )
    asyncio.run(migrate(database))


if __name__ == "__main__":
    main()
