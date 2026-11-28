"""Who is calling. Built by the identity provider from a verified token."""

from __future__ import annotations

from dataclasses import dataclass

from mlops.kernel.vocabulary import Role


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    roles: frozenset[Role]
    tenant_id: str = "default"
