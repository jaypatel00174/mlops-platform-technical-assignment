"""Authentication: turn a bearer token into a Principal.

Two adapters of the IdentityProvider port:

* DevJwtIdentityProvider: HMAC-signed tokens for local development and tests. Refused in production.
* OidcJwtIdentityProvider: tokens signed by any OIDC provider, verified against its JWKS.

Expiry is checked against an injectable clock, not PyJWT's wall clock, so behaviour is testable.
Failures never explain why a token was rejected beyond a short, safe reason.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Protocol

import jwt
import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from mlops.kernel.errors import Unauthenticated
from mlops.kernel.identity import Principal
from mlops.kernel.vocabulary import Role

CLOCK_SKEW = timedelta(seconds=30)
_ASYMMETRIC = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256"]


class IdentityProvider(Protocol):
    def authenticate(self, token: str) -> Principal:
        """Return the caller or raise Unauthenticated."""
        ...


def issue_dev_token(
    *,
    secret: str,
    subject: str,
    roles: Collection[Role],
    issuer: str,
    audience: str,
    now: datetime,
    ttl: timedelta = timedelta(hours=1),
    tenant: str | None = None,
) -> str:
    """Mint a development token. Used by tests and by `make token` for local experiments."""
    claims: dict[str, Any] = {
        "sub": subject,
        "roles": sorted(role.value for role in roles),
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if tenant is not None:
        claims["tenant"] = tenant
    return jwt.encode(claims, secret, algorithm="HS256")


def _check_times(claims: dict[str, Any], now: datetime) -> None:
    moment = now.timestamp()
    skew = CLOCK_SKEW.total_seconds()
    if moment >= claims["exp"] + skew:
        raise Unauthenticated("the token has expired")
    if claims["iat"] > moment + skew:
        raise Unauthenticated("the token was issued in the future")
    if "nbf" in claims and claims["nbf"] > moment + skew:
        raise Unauthenticated("the token is not valid yet")


def _roles(claims: dict[str, Any], claim: str, *, strict: bool) -> frozenset[Role]:
    raw = claims.get(claim) or []
    if not isinstance(raw, list):
        raise Unauthenticated(f"the {claim!r} claim must be a list of roles")
    known = {role.value: role for role in Role}
    roles: set[Role] = set()
    for item in raw:
        if item in known:
            roles.add(known[item])
        elif strict:
            raise Unauthenticated(f"the token carries an unknown role {item!r}")
        # A real identity provider issues many unrelated roles; those are simply ignored.
    return frozenset(roles)


def _principal(claims: dict[str, Any], *, roles_claim: str, strict: bool, tenant: str) -> Principal:
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise Unauthenticated("the token has no subject")
    token_tenant = claims.get("tenant")
    return Principal(
        subject=subject,
        roles=_roles(claims, roles_claim, strict=strict),
        tenant_id=token_tenant if isinstance(token_tenant, str) and token_tenant else tenant,
    )


class DevJwtIdentityProvider:
    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        audience: str,
        default_tenant: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._default_tenant = default_tenant
        self._clock = clock

    def authenticate(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["sub", "iss", "aud", "iat", "exp"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.InvalidTokenError as error:
            raise Unauthenticated("the token is invalid") from error
        _check_times(claims, self._clock())
        return _principal(claims, roles_claim="roles", strict=True, tenant=self._default_tenant)


class OidcJwtIdentityProvider:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        default_tenant: str,
        roles_claim: str = "roles",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        key_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._default_tenant = default_tenant
        self._roles_claim = roles_claim
        self._clock = clock
        # PyJWKClient caches keys, so the identity provider is not called on every request.
        self._resolve_key = key_resolver or self._jwks_resolver(jwks_url)

    @staticmethod
    def _jwks_resolver(jwks_url: str) -> Callable[[str], Any]:
        client = jwt.PyJWKClient(jwks_url, cache_keys=True)
        return lambda token: client.get_signing_key_from_jwt(token).key

    def authenticate(self, token: str) -> Principal:
        try:
            key = self._resolve_key(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=_ASYMMETRIC,
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["sub", "iss", "aud", "iat", "exp"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.PyJWTError as error:
            raise Unauthenticated("the token is invalid") from error
        _check_times(claims, self._clock())
        return _principal(
            claims, roles_claim=self._roles_claim, strict=False, tenant=self._default_tenant
        )


# --------------------------------------------------------------------------- FastAPI glue

_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="A JWT issued by the identity provider (a development token locally).",
)


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise Unauthenticated("missing bearer token")
    provider: IdentityProvider = request.app.state.identity_provider
    # Verification can block (fetching signing keys), so it runs in a worker thread. Binding the
    # log context stays here: values bound in a worker thread would not reach the request log.
    principal = await run_in_threadpool(provider.authenticate, credentials.credentials)
    structlog.contextvars.bind_contextvars(actor=principal.subject, tenant=principal.tenant_id)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
