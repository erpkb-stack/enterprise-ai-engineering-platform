"""Prompts, evaluations and experiments."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, JSONType, TenantMixin, TimestampMixin, UpdatedMixin


class PromptTemplate(IdMixin, TenantMixin, TimestampMixin, UpdatedMixin, Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    approved_version: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str | None] = mapped_column(String(36))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class PromptVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("prompt_templates.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    system: Mapped[str] = mapped_column(Text, default="")
    developer: Mapped[str] = mapped_column(Text, default="")
    user_template: Mapped[str] = mapped_column(Text, default="")
    variables: Mapped[list] = mapped_column(JSONType, default=list)
    model_config_json: Mapped[dict] = mapped_column("model_config", JSONType, default=dict)
    output_schema: Mapped[dict | None] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|in_review|approved|rejected
    change_note: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[str | None] = mapped_column(String(36))
    reviewed_by: Mapped[str | None] = mapped_column(String(36))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_comment: Mapped[str | None] = mapped_column(Text)
    execution_count: Mapped[int] = mapped_column(Integer, default=0)

    template: Mapped[PromptTemplate] = relationship(lazy="raise", foreign_keys=[template_id])


class EvaluationDataset(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20), default="golden")  # golden | redteam
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class EvaluationCase(IdMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_cases"
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_datasets.id", ondelete="CASCADE"), index=True
    )
    input: Mapped[str] = mapped_column(Text)
    variables: Mapped[dict] = mapped_column(JSONType, default=dict)
    context: Mapped[list] = mapped_column(JSONType, default=list)  # optional provided passages
    reference_answer: Mapped[str | None] = mapped_column(Text)
    expected_citations: Mapped[list] = mapped_column(JSONType, default=list)
    must_contain: Mapped[list] = mapped_column(JSONType, default=list)
    must_not_contain: Mapped[list] = mapped_column(JSONType, default=list)
    tags: Mapped[list] = mapped_column(JSONType, default=list)

    dataset: Mapped[EvaluationDataset] = relationship(lazy="raise", foreign_keys=[dataset_id])


class EvaluationRun(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_runs"
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_datasets.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), default="")
    target_type: Mapped[str] = mapped_column(String(20))  # prompt | agent | rag
    target_id: Mapped[str | None] = mapped_column(String(36))
    target_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    baseline_run_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str | None] = mapped_column(String(36))
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    dataset: Mapped[EvaluationDataset] = relationship(lazy="raise", foreign_keys=[dataset_id])


class EvaluationResult(IdMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_results"
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("evaluation_cases.id", ondelete="CASCADE"))
    output: Mapped[str] = mapped_column(Text, default="")
    scores: Mapped[dict] = mapped_column(JSONType, default=dict)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)

    run: Mapped[EvaluationRun] = relationship(lazy="raise", foreign_keys=[run_id])
    case: Mapped[EvaluationCase] = relationship(lazy="raise", foreign_keys=[case_id])


class Experiment(IdMixin, TenantMixin, TimestampMixin, UpdatedMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)
    name: Mapped[str] = mapped_column(String(200))
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    dimension: Mapped[str] = mapped_column(String(20))  # prompt | model | retrieval | agent
    status: Mapped[str] = mapped_column(String(20), default="draft")
    primary_metric: Mapped[str] = mapped_column(String(50), default="quality_score")
    min_sample_size: Mapped[int] = mapped_column(Integer, default=30)
    dataset_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("evaluation_datasets.id", ondelete="SET NULL")
    )
    created_by: Mapped[str | None] = mapped_column(String(36))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class ExperimentVariant(IdMixin, TimestampMixin, Base):
    __tablename__ = "experiment_variants"
    __table_args__ = (UniqueConstraint("experiment_id", "name"),)
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    is_control: Mapped[bool] = mapped_column(Boolean, default=False)
    traffic_weight: Mapped[float] = mapped_column(Float, default=50.0)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)

    experiment: Mapped[Experiment] = relationship(lazy="raise", foreign_keys=[experiment_id])


class ExperimentObservation(IdMixin, TimestampMixin, Base):
    __tablename__ = "experiment_observations"
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiment_variants.id", ondelete="CASCADE"), index=True
    )
    unit_id: Mapped[str] = mapped_column(String(200))
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)

    variant: Mapped[ExperimentVariant] = relationship(lazy="raise", foreign_keys=[variant_id])
