"""Token verification: the development HMAC provider and the OIDC (JWKS) provider."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mlops.kernel.errors import Unauthenticated
from mlops.kernel.vocabulary import Role
from mlops.platform.auth import (
    DevJwtIdentityProvider,
    OidcJwtIdentityProvider,
    issue_dev_token,
)

SECRET = "unit-test-secret-that-is-long-enough-for-hs256"
ISSUER = "mlops-dev"
AUDIENCE = "mlops-platform"
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def provider(**overrides: Any) -> DevJwtIdentityProvider:
    values: dict[str, Any] = {
        "secret": SECRET,
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "default_tenant": "default",
        "clock": lambda: NOW,
    }
    values.update(overrides)
    return DevJwtIdentityProvider(**values)


def token(**overrides: Any) -> str:
    values: dict[str, Any] = {
        "secret": SECRET,
        "subject": "alice",
        "roles": [Role.ML_ENGINEER],
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "now": NOW,
    }
    values.update(overrides)
    return issue_dev_token(**values)


# ------------------------------------------------------------------------------ dev tokens


def test_a_valid_token_becomes_a_principal() -> None:
    principal = provider().authenticate(token(roles=[Role.APPROVER, Role.VIEWER], tenant="acme"))

    assert principal.subject == "alice"
    assert principal.roles == {Role.APPROVER, Role.VIEWER}
    assert principal.tenant_id == "acme"


def test_the_tenant_defaults_to_the_configured_one() -> None:
    assert provider(default_tenant="plant-a").authenticate(token()).tenant_id == "plant-a"


def test_a_token_without_roles_is_valid_but_powerless() -> None:
    assert provider().authenticate(token(roles=[])).roles == frozenset()


def test_an_expired_token_is_refused() -> None:
    expired = token(now=NOW - timedelta(hours=3), ttl=timedelta(hours=1))

    with pytest.raises(Unauthenticated, match="expired"):
        provider().authenticate(expired)


def test_a_token_signed_with_another_secret_is_refused() -> None:
    forged = token(secret="a-completely-different-secret-of-sufficient-length")

    with pytest.raises(Unauthenticated):
        provider().authenticate(forged)


@pytest.mark.parametrize("overrides", [{"issuer": "someone-else"}, {"audience": "another-service"}])
def test_the_wrong_issuer_or_audience_is_refused(overrides: dict[str, str]) -> None:
    with pytest.raises(Unauthenticated):
        provider().authenticate(token(**overrides))


def test_an_unsigned_token_is_refused() -> None:
    unsigned = jwt.encode(
        {"sub": "alice", "iss": ISSUER, "aud": AUDIENCE}, key="", algorithm="none"
    )

    with pytest.raises(Unauthenticated):
        provider().authenticate(unsigned)


@pytest.mark.parametrize("garbage", ["", "not-a-jwt", "a.b.c", "Bearer abc"])
def test_garbage_is_refused_without_leaking_why(garbage: str) -> None:
    with pytest.raises(Unauthenticated) as error:
        provider().authenticate(garbage)

    assert "Traceback" not in error.value.message


def test_an_unknown_role_in_the_token_is_refused() -> None:
    claims = {
        "sub": "alice",
        "roles": ["ml-engineer", "super-user"],
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": NOW,
        "exp": NOW + timedelta(hours=1),
    }
    bad = jwt.encode(claims, SECRET, algorithm="HS256")

    with pytest.raises(Unauthenticated, match="role"):
        provider().authenticate(bad)


def test_a_token_without_a_subject_is_refused() -> None:
    claims = {"iss": ISSUER, "aud": AUDIENCE, "iat": NOW, "exp": NOW + timedelta(hours=1)}

    with pytest.raises(Unauthenticated):
        provider().authenticate(jwt.encode(claims, SECRET, algorithm="HS256"))


# ------------------------------------------------------------------------------------ OIDC


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def oidc_token(private_key: rsa.RSAPrivateKey, **claims: Any) -> str:
    payload: dict[str, Any] = {
        "sub": "bob",
        "roles": ["approver"],
        "iss": "https://id.example.org/",
        "aud": "mlops-platform",
        "iat": NOW,
        "exp": NOW + timedelta(hours=1),
    }
    payload.update(claims)
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "key-1"})


def oidc(private_key: rsa.RSAPrivateKey, **overrides: Any) -> OidcJwtIdentityProvider:
    values: dict[str, Any] = {
        "issuer": "https://id.example.org/",
        "audience": "mlops-platform",
        "jwks_url": "https://id.example.org/.well-known/jwks.json",
        "default_tenant": "default",
        "clock": lambda: NOW,
        # The test supplies the public key instead of fetching the JWKS over the network.
        "key_resolver": lambda _token: private_key.public_key(),
    }
    values.update(overrides)
    return OidcJwtIdentityProvider(**values)


def test_an_oidc_token_signed_by_the_provider_is_accepted(rsa_key: rsa.RSAPrivateKey) -> None:
    principal = oidc(rsa_key).authenticate(oidc_token(rsa_key, tenant="acme"))

    assert principal.subject == "bob"
    assert principal.roles == {Role.APPROVER}
    assert principal.tenant_id == "acme"


def test_oidc_roles_can_come_from_a_configured_claim(rsa_key: rsa.RSAPrivateKey) -> None:
    claims = {"roles": None, "groups": ["release-manager"]}

    principal = oidc(rsa_key, roles_claim="groups").authenticate(oidc_token(rsa_key, **claims))

    assert principal.roles == {Role.RELEASE_MANAGER}


def test_an_oidc_token_signed_by_someone_else_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(Unauthenticated):
        oidc(rsa_key).authenticate(oidc_token(other))


def test_an_hmac_token_cannot_be_passed_off_to_the_oidc_provider(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    """Algorithm confusion: only asymmetric algorithms are accepted for OIDC."""
    confused = jwt.encode(
        {
            "sub": "mallory",
            "roles": ["admin"],
            "iss": "https://id.example.org/",
            "aud": "mlops-platform",
        },
        SECRET,
        algorithm="HS256",
    )

    with pytest.raises(Unauthenticated):
        oidc(rsa_key).authenticate(confused)


def test_an_oidc_token_for_another_audience_is_refused(rsa_key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(Unauthenticated):
        oidc(rsa_key).authenticate(oidc_token(rsa_key, aud="another-service"))


def test_a_failing_key_lookup_is_an_authentication_failure_not_a_crash(
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    def broken(_token: str) -> Any:
        raise jwt.PyJWKClientError("jwks unreachable")

    with pytest.raises(Unauthenticated):
        oidc(rsa_key, key_resolver=broken).authenticate(oidc_token(rsa_key))
