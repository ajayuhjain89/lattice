"""Write the API's OpenAPI schema -- the canonical HTTP contract (architecture §2).

    cd backend && uv run python scripts/export_openapi.py ../apps/web/src/api/openapi.json

The web app generates its TypeScript types from this file (``pnpm api:types``); CI
regenerates both and fails if either is out of date. The app is built with placeholder
settings (test environment, development sign-in on, so the dev-login route is part of
the contract). Nothing connects to a database: the engine is lazy and the startup check
runs only when the app is served.
"""

import json
import sys
from pathlib import Path

from pydantic import SecretStr

from lattice.api.app import create_app
from lattice.config import Environment, Settings


def main(out: Path) -> None:
    settings = Settings(
        env=Environment.TEST,
        database_url=SecretStr("postgresql+asyncpg://openapi:openapi@127.0.0.1:1/openapi"),
        dev_auth_enabled=True,
        session_cookie_secure=False,
    )
    schema = create_app(settings).openapi()
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: export_openapi.py OUT.json")
    main(Path(sys.argv[1]))
