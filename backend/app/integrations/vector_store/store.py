"""Vector store adapters.

PgVectorStore: cosine distance with pgvector (`<=>`, HNSW index) and PostgreSQL full-text search
(`websearch_to_tsquery` + `ts_rank_cd` over a GIN expression index).
InMemoryScanStore: exact scan + BM25 used on SQLite (unit tests); same interface and result shape.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Float, func, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentChunk, Embedding

_TOK = re.compile(r"[a-z0-9]+")


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    filename: str
    ordinal: int
    content: str
    char_start: int
    char_end: int
    collection: str
    metadata: dict[str, Any]
    scores: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"chunk_id": self.chunk_id, "document_id": self.document_id, "filename": self.filename,
                "ordinal": self.ordinal, "content": self.content, "char_start": self.char_start,
                "char_end": self.char_end, "collection": self.collection, "metadata": self.metadata,
                "scores": {k: round(v, 5) for k, v in self.scores.items()}}


@dataclass
class SearchFilter:
    collection: str | None = None
    document_ids: list[str] | None = None
    metadata: dict[str, Any] | None = None


def _base_query(org_id: str, f: SearchFilter):
    q = (select(DocumentChunk, Document.filename, Document.doc_metadata)
         .join(Document, Document.id == DocumentChunk.document_id)
         .where(DocumentChunk.organization_id == org_id, Document.status == "ready"))
    if f.collection:
        q = q.where(DocumentChunk.collection == f.collection)
    if f.document_ids:
        q = q.where(DocumentChunk.document_id.in_(f.document_ids))
    return q


def _meta_ok(meta: dict, wanted: dict | None) -> bool:
    return not wanted or all(str(meta.get(k)) == str(v) for k, v in wanted.items())


def _to_hit(c: DocumentChunk, filename: str, dmeta: dict) -> Hit:
    return Hit(c.id, c.document_id, filename, c.ordinal, c.content, c.char_start, c.char_end, c.collection,
               {**(dmeta or {}), **(c.chunk_metadata or {})})


class VectorStore(ABC):
    @abstractmethod
    async def semantic(self, db: AsyncSession, org_id: str, qvec: list[float], k: int, f: SearchFilter) -> list[Hit]: ...

    @abstractmethod
    async def keyword(self, db: AsyncSession, org_id: str, query: str, k: int, f: SearchFilter) -> list[Hit]: ...


class PgVectorStore(VectorStore):
    async def semantic(self, db, org_id, qvec, k, f):
        dist = Embedding.vector.op("<=>", return_type=Float)(qvec)  # cosine distance
        q = _base_query(org_id, f).add_columns(dist.label("dist")).join(Embedding, Embedding.chunk_id == DocumentChunk.id)
        if f.metadata:
            q = q.where(Document.doc_metadata.op("@>")(f.metadata))
        rows = (await db.execute(q.order_by(dist).limit(k))).all()
        hits = []
        for c, fn, dm, d in rows:
            h = _to_hit(c, fn, dm)
            sim = 1 - float(d) if d is not None else 0.0
            h.scores["semantic"] = 0.0 if math.isnan(sim) else sim  # zero vectors yield NaN distance
            hits.append(h)
        return hits

    async def keyword(self, db, org_id, query, k, f):
        # OR of sanitized terms (alnum only, bound parameter) so partial matches are ranked, not dropped
        terms = [t for t in _TOK.findall(query.lower()) if len(t) > 1][:32]
        if not terms:
            return []
        tsq = func.to_tsquery("english", " | ".join(terms))
        vec = func.to_tsvector(literal_column("'english'::regconfig"), DocumentChunk.content)
        rank = func.ts_rank_cd(vec, tsq, 32).label("rank")
        q = _base_query(org_id, f).add_columns(rank).where(vec.op("@@")(tsq))
        if f.metadata:
            q = q.where(Document.doc_metadata.op("@>")(f.metadata))
        rows = (await db.execute(q.order_by(text("rank DESC")).limit(k))).all()
        out = []
        for c, fn, dm, r in rows:
            h = _to_hit(c, fn, dm)
            h.scores["keyword"] = float(r)
            out.append(h)
        return out


class InMemoryScanStore(VectorStore):
    async def _rows(self, db, org_id, f):
        rows = (await db.execute(_base_query(org_id, f).add_columns(Embedding.vector)
                                 .join(Embedding, Embedding.chunk_id == DocumentChunk.id, isouter=True))).all()
        return [(c, fn, dm, v) for c, fn, dm, v in rows if _meta_ok(dm or {}, f.metadata)]

    async def semantic(self, db, org_id, qvec, k, f):
        out = []
        for c, fn, dm, v in await self._rows(db, org_id, f):
            if not v:
                continue
            dot = sum(a * b for a, b in zip(qvec, v, strict=False))
            na = math.sqrt(sum(a * a for a in qvec)) or 1
            nb = math.sqrt(sum(b * b for b in v)) or 1
            h = _to_hit(c, fn, dm)
            h.scores["semantic"] = dot / (na * nb)
            out.append(h)
        return sorted(out, key=lambda h: -h.scores["semantic"])[:k]

    async def keyword(self, db, org_id, query, k, f):
        rows = await self._rows(db, org_id, f)
        docs = [(_to_hit(c, fn, dm), _TOK.findall(c.content.lower())) for c, fn, dm, _ in rows]
        if not docs:
            return []
        qt = [t for t in _TOK.findall(query.lower()) if len(t) > 1]
        n = len(docs)
        avgdl = sum(len(t) for _, t in docs) / n
        df = Counter(t for _, toks in docs for t in set(toks))
        k1, b = 1.5, 0.75
        out = []
        for h, toks in docs:
            tf = Counter(toks)
            s = 0.0
            for t in qt:
                if tf[t]:
                    idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                    s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(toks) / avgdl))
            if s > 0:
                h.scores["keyword"] = s
                out.append(h)
        return sorted(out, key=lambda h: -h.scores["keyword"])[:k]


def get_store(dialect: str) -> VectorStore:
    return PgVectorStore() if dialect == "postgresql" else InMemoryScanStore()
