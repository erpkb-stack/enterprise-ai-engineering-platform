# ADR-0006: Deterministic citation verification first, LLM-as-judge second

- Status: Accepted

## Decision
Every `[n]` citation is verified against the passage actually retrieved at position n using a lexical support
score (content-word recall + best sentence overlap, light stemming, threshold 0.55). Groundedness and hallucination
rate are computed per claim. LLM-as-judge is optional, reported separately with explicit limitations, and never used
alone for pass/fail. Reranking uses a heuristic cross-scorer (coverage + bigrams + proximity) because a neural
cross-encoder would add ~500 MB of model weights to the default install; the reranker is a strategy behind the
retrieval interface and can be replaced.

## Consequences
+ Reproducible, cheap, explainable verification that catches misattributed and fabricated citations.
− Conservative on paraphrase (may under-credit valid abstractive answers). Documented in reports.
