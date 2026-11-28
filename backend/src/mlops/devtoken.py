"""Print a development bearer token:  make token ROLES=approver SUBJECT=bob

For local experiments with the API (Swagger UI, curl). Refuses to run against production
settings, where the dev_jwt backend is not accepted anyway.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from mlops.app import DEV_AUDIENCE, DEV_ISSUER
from mlops.kernel.vocabulary import Role
from mlops.platform.auth import issue_dev_token
from mlops.platform.config import get_settings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mlops.devtoken", description=__doc__)
    parser.add_argument("--subject", default="alice", help="who the token is for")
    parser.add_argument(
        "--roles",
        default="admin",
        help=f"comma-separated, from: {', '.join(role.value for role in Role)}",
    )
    parser.add_argument("--tenant", default=None, help="defaults to DEFAULT_TENANT")
    parser.add_argument("--hours", type=float, default=8.0, help="how long the token is valid")
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.app_env == "production" or settings.auth_backend != "dev_jwt":
        parser.error("development tokens can only be issued with APP_ENV!=production and dev_jwt")
    try:
        roles = [Role(name.strip()) for name in args.roles.split(",") if name.strip()]
    except ValueError as error:
        parser.error(str(error))

    print(
        issue_dev_token(
            secret=settings.dev_jwt_secret,
            subject=args.subject,
            roles=roles,
            issuer=settings.oidc_issuer or DEV_ISSUER,
            audience=settings.oidc_audience or DEV_AUDIENCE,
            now=datetime.now(UTC),
            ttl=timedelta(hours=args.hours),
            tenant=args.tenant,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
