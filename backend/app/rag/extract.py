"""Document content extraction for PDF, DOCX, TXT, Markdown and CSV."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from app.core.errors import ValidationFailedError

SUPPORTED = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".csv": "text/csv"}


def detect_type(filename: str) -> tuple[str, str]:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise ValidationFailedError(f"Unsupported file type '{ext}'", details={"supported": sorted(SUPPORTED)})
    return ext, SUPPORTED[ext]


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValidationFailedError("Could not decode text file")


def extract_text(filename: str, data: bytes) -> tuple[str, dict]:
    ext, _ = detect_type(filename)
    meta: dict = {"extension": ext}
    if ext == ".pdf":
        if not data.startswith(b"%PDF"):
            raise ValidationFailedError("File is not a valid PDF")
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages[:500]]
        meta["pages"] = len(reader.pages)
        text = "\n\n".join(f"[page {i + 1}]\n{t}" for i, t in enumerate(pages) if t.strip())
    elif ext == ".docx":
        if not data.startswith(b"PK"):
            raise ValidationFailedError("File is not a valid DOCX")
        import docx

        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        text = "\n\n".join(parts)
        meta["paragraphs"] = len(d.paragraphs)
    elif ext == ".csv":
        rows = list(csv.reader(io.StringIO(_decode(data))))
        if not rows:
            text = ""
        else:
            header, body = rows[0], rows[1:]
            meta["rows"], meta["columns"] = len(body), header
            text = "\n".join("; ".join(f"{h}: {v}" for h, v in zip(header, r, strict=False)) for r in body[:20_000])
    else:
        text = _decode(data)
    if not text.strip():
        raise ValidationFailedError("No extractable text found in document")
    return text, meta
