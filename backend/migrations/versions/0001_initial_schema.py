"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-28

Registry, governance, deployment and monitoring tables, the concurrency guards (partial unique
index for one in-flight deployment, optimistic-lock counters, idempotency keys) and the audit
append-only triggers. Written by hand so every constraint is deliberate and named.

Expand-and-contract rule for later migrations: add nullable columns or new tables first, never
drop or rename in the same release that stops using them (docs/architecture.md, section 13).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOC = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
STAGES = "'DRAFT', 'VALIDATED', 'APPROVED', 'STAGING', 'PRODUCTION', 'ARCHIVED'"
APPROVAL = "'PENDING', 'APPROVED', 'REJECTED'"
DEPLOYMENT_STATUS = "'REQUESTED', 'VALIDATING', 'DEPLOYING', 'SUCCEEDED', 'FAILED', 'ROLLED_BACK'"
IN_FLIGHT = "status IN ('DEPLOYING', 'REQUESTED', 'VALIDATING')"


def _ts(name: str, *, nullable: bool = False) -> sa.Column[sa.DateTime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _text(name: str, size: int, *, nullable: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.String(size), nullable=nullable)


def upgrade() -> None:
    f = op.f

    op.create_table(
        "model",
        _text("id", 63),
        _text("tenant_id", 63),
        _text("name", 255),
        _text("owner", 255),
        _text("framework", 255),
        sa.Column("tags", JSON_DOC, nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=f("pk_model")),
    )
    op.create_index("ix_model_tenant_id", "model", ["tenant_id"])

    op.create_table(
        "model_version",
        _text("id", 127),
        _text("tenant_id", 63),
        _text("model_id", 63),
        _text("version", 64),
        _text("algorithm", 255),
        _text("artifact_uri", 1024),
        _text("artifact_digest", 71),
        _text("training_data_ref", 1024),
        _text("registered_by", 255),
        _text("stage", 16),
        _text("approval_status", 16),
        _text("runtime_type", 64),
        sa.Column("metadata", JSON_DOC, nullable=False),
        sa.Column("tags", JSON_DOC, nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f("pk_model_version")),
        sa.ForeignKeyConstraint(
            ["model_id"], ["model.id"], name=f("fk_model_version_model_id_model")
        ),
        sa.UniqueConstraint("tenant_id", "model_id", "version", name=f("uq_model_version_natural")),
        sa.CheckConstraint(f"stage IN ({STAGES})", name=f("ck_model_version_stage_valid")),
        sa.CheckConstraint(
            f"approval_status IN ({APPROVAL})", name=f("ck_model_version_approval_status_valid")
        ),
    )
    op.create_index("ix_model_version_model_id", "model_version", ["model_id"])

    op.create_table(
        "approval",
        _text("id", 64),
        _text("tenant_id", 63),
        _text("model_version_id", 127),
        _text("decision", 16),
        _text("decided_by", 255),
        sa.Column("comment", sa.Text(), nullable=False),
        _ts("decided_at"),
        sa.PrimaryKeyConstraint("id", name=f("pk_approval")),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["model_version.id"],
            name=f("fk_approval_model_version_id_model_version"),
        ),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED')", name=f("ck_approval_decision_valid")
        ),
    )
    op.create_index("ix_approval_model_version_id", "approval", ["model_version_id"])

    op.create_table(
        "audit_event",
        _text("id", 64),
        _text("tenant_id", 63),
        _text("actor", 255),
        _text("action", 128),
        _text("entity_type", 64),
        _text("entity_id", 127),
        _text("correlation_id", 128),
        _ts("occurred_at"),
        sa.Column("before_state", JSON_DOC, nullable=True),
        sa.Column("after_state", JSON_DOC, nullable=True),
        sa.PrimaryKeyConstraint("id", name=f("pk_audit_event")),
    )
    op.create_index("ix_audit_event_entity", "audit_event", ["entity_type", "entity_id"])
    op.create_index("ix_audit_event_occurred_at", "audit_event", ["occurred_at"])

    op.create_table(
        "deployment",
        _text("id", 64),
        _text("tenant_id", 63),
        _text("model_id", 63),
        _text("version", 64),
        _text("environment", 32),
        _text("type", 16),
        _text("status", 16),
        sa.Column("attempt", sa.Integer(), nullable=False),
        _text("failure_class", 24, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _text("requested_by", 255),
        _text("correlation_id", 128),
        _text("idempotency_key", 128, nullable=True),
        _text("rollback_of", 64, nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f("pk_deployment")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "model_id", "version"],
            ["model_version.tenant_id", "model_version.model_id", "model_version.version"],
            name=f("fk_deployment_model_version"),
        ),
        sa.ForeignKeyConstraint(
            ["rollback_of"], ["deployment.id"], name=f("fk_deployment_rollback_of_deployment")
        ),
        sa.CheckConstraint(
            f"status IN ({DEPLOYMENT_STATUS})", name=f("ck_deployment_status_valid")
        ),
        sa.CheckConstraint("type IN ('DEPLOY', 'ROLLBACK')", name=f("ck_deployment_type_valid")),
    )
    # One in-flight deployment per model and environment, enforced by the database (ADR-003).
    op.create_index(
        "uq_deployment_in_flight",
        "deployment",
        ["tenant_id", "model_id", "environment"],
        unique=True,
        postgresql_where=sa.text(IN_FLIGHT),
        sqlite_where=sa.text(IN_FLIGHT),
    )
    op.create_index(
        "ix_deployment_model_environment_created",
        "deployment",
        ["tenant_id", "model_id", "environment", "created_at"],
    )

    op.create_table(
        "deployment_attempt",
        _text("deployment_id", 64),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        _text("outcome", 16),
        _ts("started_at"),
        _ts("finished_at", nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _text("runtime_ref", 255, nullable=True),
        sa.PrimaryKeyConstraint("deployment_id", "attempt_no", name=f("pk_deployment_attempt")),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["deployment.id"],
            name=f("fk_deployment_attempt_deployment_id_deployment"),
        ),
        sa.CheckConstraint(
            "outcome IN ('STARTED', 'SUCCEEDED', 'FAILED')",
            name=f("ck_deployment_attempt_outcome_valid"),
        ),
    )

    op.create_table(
        "environment_binding",
        _text("tenant_id", 63),
        _text("model_id", 63),
        _text("environment", 32),
        _text("current_deployment_id", 64, nullable=True),
        _text("previous_deployment_id", 64, nullable=True),
        _ts("updated_at"),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "model_id", "environment", name=f("pk_environment_binding")
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["model.id"], name=f("fk_environment_binding_model_id_model")
        ),
        sa.ForeignKeyConstraint(
            ["current_deployment_id"],
            ["deployment.id"],
            name=f("fk_environment_binding_current_deployment_id_deployment"),
        ),
        sa.ForeignKeyConstraint(
            ["previous_deployment_id"],
            ["deployment.id"],
            name=f("fk_environment_binding_previous_deployment_id_deployment"),
        ),
    )

    op.create_table(
        "outbox_event",
        _text("id", 64),
        _text("tenant_id", 63),
        _text("type", 64),
        sa.Column("payload", JSON_DOC, nullable=False),
        _text("status", 16),
        sa.Column("attempts", sa.Integer(), nullable=False),
        _ts("available_at"),
        _text("locked_by", 128, nullable=True),
        _ts("lease_until", nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=f("pk_outbox_event")),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CLAIMED', 'DONE', 'DEAD')",
            name=f("ck_outbox_event_status_valid"),
        ),
    )
    op.create_index(
        "ix_outbox_event_status_available_at", "outbox_event", ["status", "available_at"]
    )

    op.create_table(
        "idempotency_record",
        _text("tenant_id", 63),
        _text("actor", 255),
        _text("idempotency_key", 128),
        _text("request_hash", 64),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", JSON_DOC, nullable=False),
        _ts("created_at"),
        _ts("expires_at"),
        sa.PrimaryKeyConstraint(
            "tenant_id", "actor", "idempotency_key", name=f("pk_idempotency_record")
        ),
    )
    op.create_index("ix_idempotency_record_expires_at", "idempotency_record", ["expires_at"])

    op.create_table(
        "metric_sample",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        _text("tenant_id", 63),
        _text("model_id", 63),
        _text("version", 64),
        _text("environment", 32),
        _ts("ts"),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("throughput_rpm", sa.Float(), nullable=False),
        sa.Column("error_rate", sa.Float(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("drift_score", sa.Float(), nullable=False),
        sa.Column("availability", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f("pk_metric_sample")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "model_id", "version"],
            ["model_version.tenant_id", "model_version.model_id", "model_version.version"],
            name=f("fk_metric_sample_model_version"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "model_id",
            "version",
            "environment",
            "ts",
            name=f("uq_metric_sample_natural"),
        ),
    )

    _create_audit_append_only_guard()


def downgrade() -> None:
    _drop_audit_append_only_guard()
    for table in (
        "metric_sample",
        "idempotency_record",
        "outbox_event",
        "environment_binding",
        "deployment_attempt",
        "deployment",
        "audit_event",
        "approval",
        "model_version",
        "model",
    ):
        op.drop_table(table)


def _create_audit_append_only_guard() -> None:
    """The application role may insert audit rows but never change or remove them."""
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION audit_event_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'audit_event is append-only';
            END;
            $$
            """
        )
        op.execute(
            "CREATE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON audit_event "
            "FOR EACH ROW EXECUTE FUNCTION audit_event_append_only()"
        )
    elif dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER audit_event_no_{action.lower()} BEFORE {action} ON audit_event "
                "BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END"
            )


def _drop_audit_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_event_append_only ON audit_event")
        op.execute("DROP FUNCTION IF EXISTS audit_event_append_only()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS audit_event_no_update")
        op.execute("DROP TRIGGER IF EXISTS audit_event_no_delete")
