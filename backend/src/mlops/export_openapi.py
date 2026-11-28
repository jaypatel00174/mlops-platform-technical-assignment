"""Write the API contract to a file:  python -m mlops.export_openapi ../docs/openapi.json

The frontend generates its TypeScript types from this file, and CI can diff it to catch breaking
changes. No database is touched: the app is built over an unused in-memory engine.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mlops.app import create_app
from mlops.platform.config import Settings
from mlops.platform.db import make_engine, make_session_factory


def build_spec() -> dict[str, object]:
    # "staging" is not a development environment, so the dev sign-in route is not mounted and
    # the published contract describes only the real API.
    settings = Settings(app_env="staging")
    app = create_app(
        settings=settings,
        session_factory=make_session_factory(make_engine("sqlite+pysqlite://")),
    )
    spec: dict[str, object] = app.openapi()
    return spec


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m mlops.export_openapi <output.json>", file=sys.stderr)
        return 2
    Path(args[0]).write_text(json.dumps(build_spec(), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
