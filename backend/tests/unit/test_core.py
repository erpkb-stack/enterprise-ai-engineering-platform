"""Unit tests: resilience primitives, safe evaluation, crypto, chunking, stats, parsing."""

from __future__ import annotations

import asyncio

import jwt
import pytest

from app.core.errors import AuthenticationError, CircuitOpenError, OverloadedError
from app.core.resilience import BoundedExecutor, CircuitBreaker, CircuitState, RetryPolicy, retry_async
from app.core.safe_eval import UnsafeExpression, safe_eval
from app.experiments import stats
from app.rag.chunking import chunk_text
from app.security.crypto import (
    create_access_token,
    decode_access_token,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    validate_password_strength,
    verify_password,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def test_circuit_breaker_opens_half_opens_and_closes():
    clock = FakeClock()
    cb = CircuitBreaker("t", failure_threshold=2, reset_timeout_s=10, clock=clock)

    async def boom():
        raise RuntimeError("x")

    async def ok():
        return 1

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(boom)
    assert cb.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        await cb.call(ok)
    clock.t = 11
    assert cb.allow() and cb.state is CircuitState.HALF_OPEN
    assert not cb.allow(), "only one probe admitted while half-open"
    cb._probe_in_flight = False
    assert await cb.call(ok) == 1
    assert cb.state is CircuitState.CLOSED


async def test_half_open_failure_reopens():
    clock = FakeClock()
    cb = CircuitBreaker("t2", failure_threshold=1, reset_timeout_s=5, clock=clock)

    async def boom():
        raise RuntimeError

    with pytest.raises(RuntimeError):
        await cb.call(boom)
    clock.t = 6
    with pytest.raises(RuntimeError):
        await cb.call(boom)
    assert cb.state is CircuitState.OPEN


async def test_retry_stops_after_max_attempts_and_respects_no_retry():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        raise TimeoutError

    with pytest.raises(TimeoutError):
        await retry_async(flaky, RetryPolicy(max_attempts=3, base_delay_s=0.001))
    assert calls["n"] == 3

    calls["n"] = 0

    async def fatal():
        calls["n"] += 1
        raise ValueError

    with pytest.raises(ValueError):
        await retry_async(fatal, RetryPolicy(max_attempts=5, base_delay_s=0.001, no_retry_on=(ValueError,)))
    assert calls["n"] == 1


async def test_bounded_executor_applies_backpressure():
    ex = BoundedExecutor(max_concurrency=1, max_queue=1)
    gate = asyncio.Event()

    async def work():
        await gate.wait()
        return "done"

    t1 = asyncio.create_task(ex.run(work))
    await asyncio.sleep(0.01)
    t2 = asyncio.create_task(ex.run(work))
    await asyncio.sleep(0.01)
    assert ex.active == 1 and ex.waiting == 1
    with pytest.raises(OverloadedError):
        await ex.run(work)
    gate.set()
    assert await t1 == "done" and await t2 == "done"
    assert ex.rejected == 1


@pytest.mark.parametrize("expr,expected", [
    ("1 + 2 * 3", 7), ("(10 - 4) / 3", 2.0), ("len('abc') == 3", True),
    ("contains(lower(x['sev']), 'sev2')", True), ("5 if x['n'] > 3 else 0", 5),
])
async def test_safe_eval_allows_expressions(expr, expected):
    assert safe_eval(expr, {"x": {"sev": "SEV2", "n": 4}}) == expected


@pytest.mark.parametrize("expr", [
    "__import__('os').system('id')", "().__class__.__bases__", "open('/etc/passwd')", "x.__dict__",
    "[i for i in range(10)]", "lambda: 1", "9 ** 9999", "'a' * 100000",
])
async def test_safe_eval_blocks_dangerous_syntax(expr):
    with pytest.raises(UnsafeExpression):
        safe_eval(expr, {"x": {}})


async def test_password_hashing_and_strength():
    h = hash_password("Correct-Horse-9")
    assert h.startswith("$argon2id$")
    assert verify_password("Correct-Horse-9", h) and not verify_password("wrong", h)
    assert validate_password_strength("short") and not validate_password_strength("LongEnough-Pass1")


async def test_jwt_rejects_tampering_and_alg_none():
    tok = create_access_token(user_id="u1", org_id="o1", roles=["Developer"])
    assert decode_access_token(tok)["sub"] == "u1"
    head, body, sig = tok.split(".")
    with pytest.raises(AuthenticationError):
        decode_access_token(f"{head}.{body}.{sig[:-2]}xx")
    none_tok = jwt.encode({"sub": "u1", "org": "o1", "iss": "aiecc", "exp": 9999999999, "typ": "access"}, key="",
                          algorithm="none")
    with pytest.raises(AuthenticationError):
        decode_access_token(none_tok)
    expired = create_access_token(user_id="u1", org_id="o1", roles=[], ttl_s=-10)
    with pytest.raises(AuthenticationError):
        decode_access_token(expired)


async def test_secret_encryption_roundtrip():
    ct = encrypt_secret("sk-super-secret")
    assert ct and "sk-super" not in ct and decrypt_secret(ct) == "sk-super-secret"
    assert decrypt_secret("garbage") is None


async def test_chunking_overlap_and_offsets():
    text = " ".join(f"Sentence number {i} talks about topic {i % 5}." for i in range(200))
    chunks = chunk_text(text, chunk_size=300, overlap=50)
    assert len(chunks) > 5
    assert all(len(c.content) <= 360 for c in chunks)
    assert all(chunks[i + 1].start < chunks[i].end for i in range(len(chunks) - 1)), "chunks overlap"
    with pytest.raises(ValueError):
        chunk_text(text, chunk_size=50)


async def test_statistics_match_reference_values():
    assert abs(stats.t_quantile(0.975, 10) - 2.2281) < 1e-3
    assert abs(stats.t_sf_two_sided(2.0, 20) - 0.0593) < 1e-3
    w = stats.wilson(8, 10)
    assert abs(w["ci_low"] - 0.4902) < 1e-3 and abs(w["ci_high"] - 0.9433) < 1e-3
    d = stats.welch([1, 2, 3, 4, 5], [3, 4, 5, 6, 7])
    assert d["diff"] == 2 and d["ci_low"] < 0 < d["ci_high"]


async def test_significance_guard_requires_min_sample():
    a, b = [0.1, 0.2, 0.15], [0.9, 0.95, 0.92]
    v = stats.verdict(stats.welch(a, b), 3, 3, min_n=30)
    assert v["significant"] is False and v["conclusion"] == "insufficient_sample"
    a, b = [0.1 + i * 0.001 for i in range(40)], [0.9 + i * 0.001 for i in range(40)]
    v = stats.verdict(stats.welch(a, b), 40, 40, min_n=30)
    assert v["significant"] and v["conclusion"] == "treatment_better"


async def test_parse_action_tolerates_prose_and_fences():
    from app.agents.runtime import parse_action

    assert parse_action('Sure! ```json\n{"action":"final","answer":"x"}\n```')["answer"] == "x"
    assert parse_action('I will call {"thought":"t","action":"tool","tool":"calc","arguments":{"a":{"b":1}}} now')["tool"] == "calc"
    assert parse_action("no json here") is None
    assert parse_action('{"action":"rm -rf"}') is None
