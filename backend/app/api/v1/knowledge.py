from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import Pagination, get_owned, paginate, to_dict
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ValidationFailedError
from app.models import Document, DocumentChunk
from app.rag import service as rag
from app.security import audit
from app.security.principal import Principal, require

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    strategy: Literal["semantic", "keyword", "hybrid", "hybrid_rerank"] = "hybrid"
    top_k: int = Field(5, ge=1, le=20)
    collection: str | None = None
    document_ids: list[str] | None = None
    metadata: dict[str, Any] | None = None
    compress: bool = False


class AnswerIn(SearchIn):
    provider: str = "mock"
    model: str = "mock-reasoner-1"


@router.post("/documents", status_code=201, summary="Upload PDF/DOCX/TXT/MD/CSV; extract, chunk, embed, index")
async def upload(file: UploadFile = File(...), collection: str = Form("default"), chunk_size: int = Form(800),
                 chunk_overlap: int = Form(120), metadata: str = Form("{}"), db: AsyncSession = Depends(get_db),
                 p: Principal = Depends(require("knowledge:write"))) -> dict:
    data = await file.read(get_settings().upload_max_bytes + 1)
    try:
        meta = json.loads(metadata or "{}")
        if not isinstance(meta, dict):
            raise ValueError
    except ValueError as exc:
        raise ValidationFailedError("metadata must be a JSON object") from exc
    doc = await rag.ingest_document(db, org_id=p.org_id, user_id=p.user_id, filename=file.filename or "upload.txt",
                                    data=data, collection=collection[:100], chunk_size=chunk_size,
                                    chunk_overlap=chunk_overlap, metadata=meta)
    await audit.record(db, org_id=p.org_id, action="knowledge.upload", category="data", actor_id=p.user_id,
                       actor_email=p.email, resource_type="document", resource_id=doc.id,
                       details={"filename": doc.filename, "chunks": doc.chunk_count, "bytes": doc.size_bytes})
    return to_dict(doc)


@router.get("/documents")
async def list_documents(collection: str | None = None, page: Pagination = Depends(), db: AsyncSession = Depends(get_db),
                         p: Principal = Depends(require("knowledge:read"))) -> dict:
    stmt = select(Document).where(Document.organization_id == p.org_id)
    if collection:
        stmt = stmt.where(Document.collection == collection)
    return await paginate(db, stmt.order_by(Document.created_at.desc()), page)


@router.get("/collections")
async def collections(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("knowledge:read"))) -> list[dict]:
    rows = (await db.execute(select(Document.collection, func.count(), func.sum(Document.chunk_count))
                             .where(Document.organization_id == p.org_id).group_by(Document.collection))).all()
    return [{"collection": c, "documents": n, "chunks": int(ch or 0)} for c, n, ch in rows]


@router.get("/documents/{doc_id}/chunks", summary="Inspect chunks with offsets (provenance)")
async def chunks(doc_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("knowledge:read"))) -> dict:
    d = await get_owned(db, Document, doc_id, p.org_id)
    rows = (await db.execute(select(DocumentChunk).where(DocumentChunk.document_id == d.id).order_by(DocumentChunk.ordinal))).scalars()
    return {"document": to_dict(d), "chunks": [to_dict(c) for c in rows]}


@router.delete("/documents/{doc_id}")
async def delete(doc_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("knowledge:write"))) -> dict:
    d = await get_owned(db, Document, doc_id, p.org_id)
    await rag.delete_document(db, p.org_id, d.id)
    await audit.record(db, org_id=p.org_id, action="knowledge.delete", category="data", actor_id=p.user_id,
                       actor_email=p.email, resource_type="document", resource_id=d.id, details={"filename": d.filename})
    return {"ok": True}


@router.post("/search", summary="Retrieval with per-strategy score breakdown (debugging interface)")
async def search(body: SearchIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("knowledge:read"))) -> dict:
    return await rag.search(db, org_id=p.org_id, query=body.query, strategy=body.strategy, top_k=body.top_k,
                            collection=body.collection, document_ids=body.document_ids, metadata=body.metadata,
                            compress_context=body.compress)


@router.post("/answer", summary="Grounded answer with citations verified against retrieved passages")
async def answer(body: AnswerIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("knowledge:read"))) -> dict:
    res = await rag.answer(db, org_id=p.org_id, question=body.query, provider=body.provider, model=body.model,
                           strategy=body.strategy, top_k=body.top_k, collection=body.collection, compress_context=body.compress)
    await audit.record(db, org_id=p.org_id, action="knowledge.answer", category="model_invocation", actor_id=p.user_id,
                       actor_email=p.email, details={"provider": res["provider"], "model": res["model"],
                                                     "groundedness": res["verification"]["groundedness"]})
    return res
