"""Tables owned by the monitoring module.

`metric_sample` is a plain table in the initial migration. A PostgreSQL-only migration can later
convert it to monthly range partitions and add rollups (docs/architecture.md, section 11).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Float, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mlops.platform.db import Base, UTCDateTime


class MetricSampleRow(Base):
    __tablename__ = "metric_sample"

    # BIGINT on PostgreSQL; SQLite only auto-increments a plain INTEGER primary key.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = mapped_column(String(63))
    model_id: Mapped[str] = mapped_column(String(63))
    version: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(UTCDateTime)
    latency_ms: Mapped[float] = mapped_column(Float)
    throughput_rpm: Mapped[float] = mapped_column(Float)
    error_rate: Mapped[float] = mapped_column(Float)
    quality_score: Mapped[float] = mapped_column(Float)
    drift_score: Mapped[float] = mapped_column(Float)
    availability: Mapped[float] = mapped_column(Float)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "model_id", "version"],
            ["model_version.tenant_id", "model_version.model_id", "model_version.version"],
            name="fk_metric_sample_model_version",
        ),
        # Doubles as the lookup index and makes ingest and seeding idempotent.
        UniqueConstraint(
            "tenant_id", "model_id", "version", "environment", "ts", name="uq_metric_sample_natural"
        ),
    )
