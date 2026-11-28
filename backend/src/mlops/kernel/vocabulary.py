"""Vocabulary shared by several modules.

Modules may not import each other's domain code, so concepts that more than one module needs
(lifecycle stage, approval status, role, environment names) live in this small shared kernel.
"""

from __future__ import annotations

from enum import StrEnum


class Stage(StrEnum):
    """Lifecycle stage of a model version. Changes only through registry.domain.lifecycle."""

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"
    ARCHIVED = "ARCHIVED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Role(StrEnum):
    VIEWER = "viewer"
    ML_ENGINEER = "ml-engineer"
    APPROVER = "approver"
    RELEASE_MANAGER = "release-manager"
    ADMIN = "admin"
    SERVICE = "service"  # machine identity for metric agents


# Environment names the platform governs. Other names are validated by governance policy.
DEV = "dev"
STAGING = "staging"
PRODUCTION = "production"
