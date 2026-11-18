"""RAG knowledge base: documents, chunks and embeddings (pgvector)."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.models.base import Base, EmbeddingVector, IdMixin, JSONType, TenantMixin, TimestampMixin

EMBED_DIM = get_settings().embedding_dim


class Document(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("organization_id", "collection", "sha256"),)
    collection: Mapped[str] = mapped_column(String(100), default="default", index=True)
    filename: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="processing")
    error: Mapped[str | None] = mapped_column(Text)
    doc_metadata: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_size: Mapped[int] = mapped_column(Integer, default=800)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=120)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(String(36))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class DocumentChunk(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal"),)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    collection: Mapped[str] = mapped_column(String(100), default="default", index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)

    document: Mapped[Document] = relationship(lazy="raise", foreign_keys=[document_id])


class Embedding(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "embeddings"
    chunk_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("document_chunks.id", ondelete="CASCADE"), unique=True, index=True
    )
    model: Mapped[str] = mapped_column(String(200))
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list[float]] = mapped_column(EmbeddingVector(EMBED_DIM))

    chunk: Mapped[DocumentChunk] = relationship(lazy="raise", foreign_keys=[chunk_id])
