"""Development sign-in: trade a persona for a token, so the UI works without an identity provider.

This endpoint hands out tokens to anyone who asks, so it exists only when the process runs with
`AUTH_BACKEND=dev_jwt` and `APP_ENV` is `local` or `test`. Staging and production never mount it
(production is also refused earlier by the settings check). Real deployments sign in through an
OIDC provider and present its tokens.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from mlops.kernel.ports import Clock
from mlops.kernel.vocabulary import Role
from mlops.platform.auth import issue_dev_token
from mlops.platform.config import Settings

DEV_LOGIN_ENVIRONMENTS = frozenset({"local", "test"})
_TOKEN_TTL = timedelta(hours=8)


def dev_login_enabled(settings: Settings) -> bool:
    return settings.auth_backend == "dev_jwt" and settings.app_env in DEV_LOGIN_ENVIRONMENTS


class DevLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    roles: list[Role] = Field(max_length=len(Role))


class DevLoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105  (the OAuth token type, not a secret)
    subject: str
    roles: list[Role]
    expires_at: datetime


def build_dev_login_router(
    settings: Settings, clock: Clock, *, issuer: str, audience: str
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/dev", tags=["development"])

    @router.post(
        "/token",
        response_model=DevLoginResponse,
        summary="Development only: get a token for a persona",
        description="Mounted only when AUTH_BACKEND=dev_jwt and APP_ENV is local or test.",
    )
    def dev_token(body: DevLoginRequest) -> DevLoginResponse:
        now = clock.now()
        token = issue_dev_token(
            secret=settings.dev_jwt_secret,
            subject=body.subject,
            roles=body.roles,
            issuer=issuer,
            audience=audience,
            now=now,
            ttl=_TOKEN_TTL,
            tenant=settings.default_tenant,
        )
        return DevLoginResponse(
            access_token=token,
            subject=body.subject,
            roles=body.roles,
            expires_at=now + _TOKEN_TTL,
        )

    return router
