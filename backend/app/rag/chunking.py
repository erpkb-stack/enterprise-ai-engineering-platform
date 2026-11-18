"""Recursive character chunker with overlap that preserves source offsets for citation provenance."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " "]


@dataclass
class Chunk:
    content: str
    start: int
    end: int


def _split(text: str, size: int, seps: list[str]) -> list[str]:
    if len(text) <= size:
        return [text]
    for i, sep in enumerate(seps):
        if sep in text:
            parts = text.split(sep)
            out: list[str] = []
            for j, p in enumerate(parts):
                piece = p + (sep if j < len(parts) - 1 else "")
                if len(piece) > size:
                    out.extend(_split(piece, size, seps[i + 1 :]))
                else:
                    out.append(piece)
            return out
    return [text[k : k + size] for k in range(0, len(text), size)]


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[Chunk]:
    if chunk_size < 100:
        raise ValueError("chunk_size must be >= 100")
    if not 0 <= overlap < chunk_size // 2:
        raise ValueError("overlap must be >= 0 and < chunk_size/2")
    text = re.sub(r"[ \t]+", " ", text).strip()
    pieces = _split(text, chunk_size, _SEPARATORS)
    chunks: list[Chunk] = []
    buf, buf_start, pos = "", 0, 0
    for p in pieces:
        if buf and len(buf) + len(p) > chunk_size:
            chunks.append(Chunk(buf.strip(), buf_start, buf_start + len(buf)))
            tail = buf[-overlap:] if overlap else ""
            # start overlap at a word boundary
            if overlap and " " in tail:
                tail = tail[tail.index(" ") + 1 :]
            buf_start = buf_start + len(buf) - len(tail)
            buf = tail
        if not buf:
            buf_start = pos
        buf += p
        pos += len(p)
    if buf.strip():
        chunks.append(Chunk(buf.strip(), buf_start, buf_start + len(buf)))
    return [c for c in chunks if len(c.content) >= 20 or len(chunks) == 1]
