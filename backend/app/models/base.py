from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, String, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app.core.db import Base
from app.core.ids import new_id, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")


class EmbeddingVector(TypeDecorator):
    """pgvector on PostgreSQL; JSON array elsewhere (SQLite for fast unit tests)."""

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):  # type: ignore[override]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        if value is None:
            return None
        return list(map(float, value))

    def process_result_value(self, value, dialect):  # type: ignore[override]
        if value is None:
            return None
        return [float(x) for x in value]


class IdMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class UpdatedMixin:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class TenantMixin:
    """Every tenant-owned row carries organization_id; repositories always filter on it."""

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )

    @declared_attr
    def organization(cls):  # noqa: N805 - many-to-one so the unit of work orders INSERTs correctly
        return relationship("Organization", lazy="raise", foreign_keys=[cls.organization_id])


__all__ = ["Base", "EmbeddingVector", "IdMixin", "JSONType", "TenantMixin", "TimestampMixin", "UpdatedMixin"]
