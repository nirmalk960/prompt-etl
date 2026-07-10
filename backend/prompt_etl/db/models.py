"""SQLAlchemy ORM models.

Schema (Phase 1):

  users        - minimal user table; populated by dev-token auth stub in Phase 1.
  workspaces   - tenancy scope (single default workspace per user in Phase 1).
  datasets     - one row per uploaded file; tracks raw key and parquet key.
  workflows    - saved pipeline definitions (JSON DAG).
  jobs         - one row per pipeline run; status, engine used, started/ended at.
  job_events   - durable step events (also published to Redis pubsub for live SSE).
                 Used for resume-from-event-id semantics.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

# Use JSONB on Postgres for indexable JSON; gracefully fall back to JSON elsewhere.
JsonType = JSONB().with_variant(JSON(), "sqlite")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    workspaces: Mapped[list["Workspace"]] = relationship(back_populates="owner")


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="workspaces")
    datasets: Mapped[list["Dataset"]] = relationship(back_populates="workspace")
    workflows: Mapped[list["Workflow"]] = relationship(back_populates="workspace")

    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_workspaces_owner_name"),)


class Dataset(Base, TimestampMixin):
    """A user-uploaded dataset, indexed by its parquet form for efficient scans."""

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    source_format: Mapped[str] = mapped_column(String(20), nullable=False)  # csv|json|xlsx|parquet
    raw_object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    parquet_object_key: Mapped[str | None] = mapped_column(String(1024))
    raw_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    parquet_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    schema_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(
        Enum(
            "uploading",
            "ingesting",
            "ready",
            "failed",
            name="dataset_status",
            native_enum=False,
        ),
        default="uploading",
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(Text)

    workspace: Mapped[Workspace] = relationship(back_populates="datasets")

    __table_args__ = (Index("ix_datasets_workspace_status", "workspace_id", "status"),)


class Workflow(Base, TimestampMixin):
    """A saved pipeline definition (DAG of nodes and edges)."""

    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    definition: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="workflows")


class Job(Base, TimestampMixin):
    """One pipeline run. Created by POST /jobs, updated by the worker."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL")
    )
    definition: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    engine: Mapped[str | None] = mapped_column(String(40))  # pandas|polars|duckdb|spark
    status: Mapped[str] = mapped_column(
        Enum(
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            name="job_status",
            native_enum=False,
        ),
        default="queued",
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_dataset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)

    events: Mapped[list["JobEvent"]] = relationship(
        back_populates="job", order_by="JobEvent.seq", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_jobs_workspace_status", "workspace_id", "status"),)


class JobEvent(Base):
    """Durable per-step event. Also published to Redis pubsub channel `job:{job_id}`.

    `seq` is a monotonic counter scoped to the job, used as the SSE event id so
    a reconnecting client can resume from the last seen seq.
    """

    __tablename__ = "job_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    job: Mapped[Job] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint("job_id", "seq", name="uq_job_events_job_seq"),
        Index("ix_job_events_job_seq", "job_id", "seq"),
    )
