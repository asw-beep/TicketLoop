"""The API the browser tests talk to, on a database of its own.

Playwright starts this (frontend/playwright.config.ts). Every run drops `ticketloop_e2e`,
creates it and migrates it, so a run never sees rows an earlier run, or a person, left behind.

    DATABASE_URL=.../ticketloop_e2e uv run python -m scripts.e2e_api --port 8001
"""

import argparse
import sys

import uvicorn
from sqlalchemy import make_url

from app.core.config import get_settings
from app.infrastructure.database.engine import create_db_engine
from scripts.databases import is_throwaway, recreate_database, upgrade


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args(argv)

    settings = get_settings()
    name = make_url(settings.database_url).database
    if not is_throwaway(name, ("_e2e",)):
        print(f"Refusing: {name!r} does not end in _e2e. Set DATABASE_URL.", file=sys.stderr)
        return 2
    recreate_database(settings.database_url)
    engine = create_db_engine(settings)
    upgrade(engine)
    engine.dispose()

    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
