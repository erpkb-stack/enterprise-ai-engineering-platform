"""Measured (never hardcoded) runtime metrics.

Two sinks:
  * Prometheus client metrics (scraped at /metrics by Prometheus/Grafana)
  * An in-process rolling window used by the dashboard API for P50/P95/P99, RPS and time series.

Every sample carries a `synthetic` flag so load-test traffic is always distinguishable from organic traffic.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import psutil
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry()
HTTP_LATENCY = Histogram(
    "aiecc_http_request_duration_seconds", "HTTP request latency", ["method", "route", "status_class", "synthetic"],
    registry=REGISTRY, buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
OP_LATENCY = Histogram(
    "aiecc_operation_duration_seconds", "Latency of LLM/tool/agent/workflow operations", ["kind", "name", "success"],
    registry=REGISTRY, buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
TOKENS = Counter("aiecc_llm_tokens_total", "LLM tokens", ["provider", "model", "direction"], registry=REGISTRY)
COST = Counter("aiecc_llm_cost_usd_total", "Estimated LLM cost (USD)", ["provider", "model"], registry=REGISTRY)
RETRIES = Counter("aiecc_retries_total", "Retry attempts", ["kind", "name"], registry=REGISTRY)
ERRORS = Counter("aiecc_errors_total", "Errors", ["kind", "name"], registry=REGISTRY)
INFLIGHT = Gauge("aiecc_executions_inflight", "In-flight executions", registry=REGISTRY)
QUEUE_DEPTH = Gauge("aiecc_execution_queue_depth", "Queued executions waiting for capacity", registry=REGISTRY)


@dataclass(slots=True)
class Sample:
    ts: float
    latency_ms: float
    ok: bool
    synthetic: bool


def percentile(sorted_vals: list[float], p: float) -> float | None:
    """Nearest-rank with linear interpolation (same as numpy 'linear')."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p / 100.0
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


class RollingWindow:
    def __init__(self, maxlen: int = 20_000) -> None:
        self._data: dict[str, deque[Sample]] = defaultdict(lambda: deque(maxlen=maxlen))
        self._lock = threading.Lock()
        self.retries: dict[str, int] = defaultdict(int)

    def add(self, key: str, latency_ms: float, ok: bool, synthetic: bool = False) -> None:
        with self._lock:
            self._data[key].append(Sample(time.time(), latency_ms, ok, synthetic))

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._data)

    def samples(self, key: str | None = None, window_s: float = 900, synthetic: bool | None = None) -> list[Sample]:
        cutoff = time.time() - window_s
        with self._lock:
            series = [self._data[key]] if key else list(self._data.values())
            out = [s for d in series for s in d if s.ts >= cutoff]
        if synthetic is not None:
            out = [s for s in out if s.synthetic == synthetic]
        return out

    @staticmethod
    def summarize(samples: list[Sample], window_s: float) -> dict:
        lat = sorted(s.latency_ms for s in samples)
        n = len(samples)
        errs = sum(1 for s in samples if not s.ok)
        span = window_s
        if n >= 2:
            span = max(1.0, min(window_s, time.time() - min(s.ts for s in samples)))
        return {
            "count": n,
            "errors": errs,
            "error_rate": (errs / n) if n else None,
            "rps": (n / span) if n else 0.0,
            "p50_ms": percentile(lat, 50),
            "p95_ms": percentile(lat, 95),
            "p99_ms": percentile(lat, 99),
            "max_ms": lat[-1] if lat else None,
            "mean_ms": (sum(lat) / n) if n else None,
        }

    def timeseries(self, prefix: str, window_s: float = 900, bucket_s: int = 15) -> list[dict]:
        now = time.time()
        start = now - window_s
        buckets: dict[int, list[Sample]] = defaultdict(list)
        with self._lock:
            for key, d in self._data.items():
                if not key.startswith(prefix):
                    continue
                for s in d:
                    if s.ts >= start:
                        buckets[int((s.ts - start) // bucket_s)].append(s)
        out = []
        for i in range(int(window_s // bucket_s)):
            ss = buckets.get(i, [])
            lat = sorted(s.latency_ms for s in ss)
            out.append({
                "t": int(start + i * bucket_s),
                "requests": len(ss),
                "synthetic": sum(1 for s in ss if s.synthetic),
                "errors": sum(1 for s in ss if not s.ok),
                "rps": round(len(ss) / bucket_s, 3),
                "p50_ms": percentile(lat, 50),
                "p95_ms": percentile(lat, 95),
                "p99_ms": percentile(lat, 99),
            })
        return out


window = RollingWindow()
_proc = psutil.Process(os.getpid())
_proc.cpu_percent(None)


def record_http(method: str, route: str, status: int, latency_ms: float, synthetic: bool) -> None:
    HTTP_LATENCY.labels(method, route, f"{status // 100}xx", str(synthetic).lower()).observe(latency_ms / 1000)
    window.add(f"http:{method} {route}", latency_ms, status < 500, synthetic)


def record_op(kind: str, name: str, latency_ms: float, ok: bool, synthetic: bool = False) -> None:
    OP_LATENCY.labels(kind, name, str(ok).lower()).observe(latency_ms / 1000)
    window.add(f"{kind}:{name}", latency_ms, ok, synthetic)
    if not ok:
        ERRORS.labels(kind, name).inc()


def record_retry(kind: str, name: str) -> None:
    RETRIES.labels(kind, name).inc()
    window.retries[f"{kind}:{name}"] += 1


def record_tokens(provider: str, model: str, tokens_in: int, tokens_out: int, cost: float) -> None:
    TOKENS.labels(provider, model, "in").inc(tokens_in)
    TOKENS.labels(provider, model, "out").inc(tokens_out)
    COST.labels(provider, model).inc(cost)


def system_snapshot() -> dict:
    mem = _proc.memory_info()
    vm = psutil.virtual_memory()
    return {
        "process_cpu_percent": _proc.cpu_percent(None),
        "system_cpu_percent": psutil.cpu_percent(None),
        "process_rss_mb": round(mem.rss / 1024 / 1024, 1),
        "system_memory_percent": vm.percent,
        "threads": _proc.num_threads(),
        "open_fds": _proc.num_fds() if hasattr(_proc, "num_fds") else None,
        "source": "psutil (measured)",
    }


def prometheus_text() -> bytes:
    return generate_latest(REGISTRY)
