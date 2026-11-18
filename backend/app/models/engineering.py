"""Workflows, architecture lab, code review, audit and usage metrics."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, JSONType, TenantMixin, TimestampMixin, UpdatedMixin


class Workflow(IdMixin, TenantMixin, TimestampMixin, UpdatedMixin, Base):
    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[str | None] = mapped_column(String(36))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkflowVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version"),)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    graph: Mapped[dict] = mapped_column(JSONType)
    change_note: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[str | None] = mapped_column(String(36))

    workflow: Mapped[Workflow] = relationship(lazy="raise", foreign_keys=[workflow_id])


class WorkflowExecution(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "workflow_executions"
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    input: Mapped[dict] = mapped_column(JSONType, default=dict)
    output: Mapped[dict | None] = mapped_column(JSONType)
    state: Mapped[dict] = mapped_column(JSONType, default=dict)
    trace: Mapped[list] = mapped_column(JSONType, default=list)
    paused_at_node: Mapped[str | None] = mapped_column(String(100))
    pause_reason: Mapped[str | None] = mapped_column(String(30))  # approval | breakpoint
    breakpoints: Mapped[list] = mapped_column(JSONType, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(36))
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workflow: Mapped[Workflow] = relationship(lazy="raise", foreign_keys=[workflow_id])


class ArchitectureDesign(IdMixin, TenantMixin, TimestampMixin, UpdatedMixin, Base):
    __tablename__ = "architecture_designs"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(40), default="microservices")
    graph: Mapped[dict] = mapped_column(JSONType, default=dict)
    sequence_diagram: Mapped[str] = mapped_column(Text, default="")
    data_flow: Mapped[str] = mapped_column(Text, default="")
    api_contracts: Mapped[str] = mapped_column(Text, default="")
    requirements: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(36))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class ArchitectureReview(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "architecture_reviews"
    design_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("architecture_designs.id", ondelete="CASCADE"), index=True
    )
    report: Mapped[dict] = mapped_column(JSONType)
    created_by: Mapped[str | None] = mapped_column(String(36))

    design: Mapped[ArchitectureDesign] = relationship(lazy="raise", foreign_keys=[design_id])


class ArchitectureDecisionRecord(IdMixin, TenantMixin, TimestampMixin, UpdatedMixin, Base):
    __tablename__ = "adrs"
    __table_args__ = (UniqueConstraint("organization_id", "number"),)
    design_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("architecture_designs.id", ondelete="SET NULL")
    )
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    context: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(Text, default="")
    consequences: Mapped[str] = mapped_column(Text, default="")
    alternatives: Mapped[list] = mapped_column(JSONType, default=list)
    created_by: Mapped[str | None] = mapped_column(String(36))


class CodeReview(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "code_reviews"
    filename: Mapped[str] = mapped_column(String(300), default="snippet.py")
    code: Mapped[str] = mapped_column(Text)
    static_findings: Mapped[list] = mapped_column(JSONType, default=list)
    ai_review: Mapped[dict] = mapped_column(JSONType, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(36))


class AuditEvent(IdMixin, Base):
    """Append-only, hash-chained audit log. UPDATE/DELETE are blocked by a DB trigger on PostgreSQL
    and chain integrity can be verified via /api/v1/audit/verify."""

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "chain_index"),
        Index("ix_audit_org_created", "organization_id", "created_at"),
    )
    organization_id: Mapped[str | None] = mapped_column(String(36), index=True)
    chain_index: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(100), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(36))
    outcome: Mapped[str] = mapped_column(String(20), default="success")
    severity: Mapped[str] = mapped_column(String(10), default="info")
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class UsageMetric(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "usage_metrics"
    __table_args__ = (Index("ix_usage_org_kind_created", "organization_id", "kind", "created_at"),)
    kind: Mapped[str] = mapped_column(String(20))  # llm | tool | agent | workflow | embedding | retrieval
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(200))
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
