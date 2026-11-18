"""Deterministic grounding, citation verification and hallucination detection.

Never trust a model's citation: each `[n]` marker is checked against the passage actually
retrieved at position n. A citation is VALID only if (a) passage n exists and (b) the cited
sentence's content words are substantially supported by that passage.

Method (documented limitation): lexical support = max(content-word recall of the claim in the
passage, best sentence-level overlap) with light stemming. It is precise for extractive answers
and conservative for paraphrase (may under-credit valid paraphrases). An optional LLM judge can be
layered on top in the evaluation engine; its results are always reported separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_CIT = re.compile(r"\[(\d{1,3})\]")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n+")
_TOK = re.compile(r"[a-z0-9]+")
_STOP = set(
    "a an the of to in on for and or is are was were be been being it its this that these those with as by at from "
    "into than then there their they them we you your our i he she his her not no but if so such can could should "
    "would will may might must do does did has have had also which who whom what when where why how all any each "
    "based tool data according".split()
)

SUPPORT_THRESHOLD = 0.55


def _stem(t: str) -> str:
    for suf in ("ing", "edly", "ed", "es", "s", "ly"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def content_words(text: str) -> list[str]:
    return [_stem(t) for t in _TOK.findall(text.lower()) if t not in _STOP and len(t) > 1]


def support_score(claim: str, passage: str) -> float:
    cw = set(content_words(claim))
    if not cw:
        return 1.0
    pw = set(content_words(passage))
    recall = len(cw & pw) / len(cw)
    best = 0.0
    for s in _SENT.split(passage):
        sw = set(content_words(s))
        if sw:
            best = max(best, len(cw & sw) / len(cw))
    return max(recall * 0.9, best)


_TRAILING_CIT = re.compile(r"([.!?])((?:\s*\[\d{1,3}\])+)")


def split_claims(answer: str) -> list[str]:
    # normalise "claim. [1][2] Next" -> "claim [1][2]. Next" so citations stay with their sentence
    answer = _TRAILING_CIT.sub(lambda m: m.group(2) + m.group(1), answer)
    parts = [p.strip() for p in _SENT.split(answer) if p and p.strip()]
    return [p for p in parts if len(content_words(_CIT.sub("", p))) >= 3]


@dataclass
class ClaimCheck:
    claim: str
    citations: list[int]
    valid_citations: list[int]
    invalid_citations: list[dict[str, Any]]
    best_passage: int | None
    best_support: float
    supported: bool

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__ | {"best_support": round(self.best_support, 3)}


@dataclass
class GroundingReport:
    claims: list[ClaimCheck] = field(default_factory=list)
    total_citations: int = 0
    valid_citations: int = 0
    nonexistent_citations: int = 0

    @property
    def citation_precision(self) -> float | None:
        return self.valid_citations / self.total_citations if self.total_citations else None

    @property
    def groundedness(self) -> float | None:
        return sum(c.supported for c in self.claims) / len(self.claims) if self.claims else None

    @property
    def hallucination_rate(self) -> float | None:
        g = self.groundedness
        return None if g is None else 1 - g

    @property
    def citation_coverage(self) -> float | None:
        return sum(bool(c.citations) for c in self.claims) / len(self.claims) if self.claims else None

    def as_dict(self) -> dict[str, Any]:
        r = lambda v: None if v is None else round(v, 4)  # noqa: E731
        return {
            "method": "deterministic-lexical-support-v1",
            "threshold": SUPPORT_THRESHOLD,
            "claims": [c.as_dict() for c in self.claims],
            "claim_count": len(self.claims),
            "total_citations": self.total_citations,
            "valid_citations": self.valid_citations,
            "nonexistent_citations": self.nonexistent_citations,
            "citation_precision": r(self.citation_precision),
            "citation_coverage": r(self.citation_coverage),
            "groundedness": r(self.groundedness),
            "hallucination_rate": r(self.hallucination_rate),
        }


def verify(answer: str, passages: list[str], threshold: float = SUPPORT_THRESHOLD) -> GroundingReport:
    """`passages[i]` corresponds to citation marker [i+1]."""
    rep = GroundingReport()
    for claim in split_claims(answer):
        cites = [int(n) for n in _CIT.findall(claim)]
        bare = _CIT.sub("", claim).strip()
        valid, invalid = [], []
        for n in cites:
            rep.total_citations += 1
            if not 1 <= n <= len(passages):
                rep.nonexistent_citations += 1
                invalid.append({"citation": n, "reason": "cited passage does not exist"})
                continue
            sc = support_score(bare, passages[n - 1])
            if sc >= threshold:
                valid.append(n)
                rep.valid_citations += 1
            else:
                invalid.append({"citation": n, "reason": "passage does not support claim", "support": round(sc, 3)})
        scores = [(support_score(bare, p), i + 1) for i, p in enumerate(passages)]
        best_s, best_i = max(scores) if scores else (0.0, None)
        rep.claims.append(ClaimCheck(bare, cites, valid, invalid, best_i, best_s, best_s >= threshold))
    return rep
