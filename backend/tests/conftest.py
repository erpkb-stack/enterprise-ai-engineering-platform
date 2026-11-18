"""Test harness.

Default: isolated SQLite database (schema via metadata.create_all) + in-memory Redis fallback, so the suite
runs anywhere without Docker. Set TEST_DATABASE_URL=postgresql+asyncpg://... to run the same suite against
PostgreSQL + pgvector with Alembic migrations (tests marked `postgres` only run in that mode).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="aiecc-test-"))
PG_URL = os.environ.get("TEST_DATABASE_URL")
os.environ.update({
    "AIECC_ENV": "test",
    "AIECC_DATABASE_URL": PG_URL or f"sqlite+aiosqlite:///{_TMP / 'test.db'}",
    "AIECC_REDIS_URL": "",
    "AIECC_LOG_JSON": "false",
    "AIECC_LOG_LEVEL": "WARNING",
    "AIECC_RATE_LIMIT_PER_MINUTE": "1000000",
    "AIECC_RATE_LIMIT_AUTH_PER_MINUTE": "1000000",
    "AIECC_JOBS_MODE": "inline",
    "AIECC_EMBEDDING_PROVIDER": "mock",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.core.config import get_settings  # noqa: E402

PASSWORD = get_settings().demo_password.get_secret_value()
API = "/api/v1"


def pytest_collection_modifyitems(config, items):
    skip_pg = pytest.mark.skip(reason="requires TEST_DATABASE_URL (PostgreSQL)")
    skip_ollama = pytest.mark.skip(reason="requires a running Ollama with llama3.2:3b (set TEST_OLLAMA=1)")
    for item in items:
        if "postgres" in item.keywords and not PG_URL:
            item.add_marker(skip_pg)
        if "ollama" in item.keywords and not os.environ.get("TEST_OLLAMA"):
            item.add_marker(skip_ollama)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def app():
    from app.core.db import Base, get_engine
    from app.main import create_app, wire_integrations
    from app.seed.demo import seed_if_empty

    engine = get_engine()
    if PG_URL:
        from sqlalchemy import text

        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "alembic", "upgrade", "head",
                                                    cwd=str(Path(__file__).resolve().parents[1]))
        assert await proc.wait() == 0, "alembic upgrade failed"
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    await wire_integrations()
    await seed_if_empty()
    application = create_app()
    yield application
    from app.agents import runtime
    from app.integrations.mcp.manager import mcp_manager

    await runtime.shutdown(2)
    await mcp_manager.shutdown_all()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as c:
        yield c


_token_cache: dict[str, str] = {}


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    if email not in _token_cache:
        r = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        _token_cache[email] = r.json()["access_token"]
    return {"Authorization": f"Bearer {_token_cache[email]}"}


@pytest_asyncio.fixture(loop_scope="session")
async def admin(client):
    return await login(client, "admin@aiecc.local")


@pytest_asyncio.fixture(loop_scope="session")
async def aieng(client):
    return await login(client, "aieng@aiecc.local")


@pytest_asyncio.fixture(loop_scope="session")
async def dev(client):
    return await login(client, "dev@aiecc.local")


@pytest_asyncio.fixture(loop_scope="session")
async def security(client):
    return await login(client, "security@aiecc.local")


@pytest_asyncio.fixture(loop_scope="session")
async def auditor(client):
    return await login(client, "auditor@aiecc.local")


@pytest_asyncio.fixture(loop_scope="session")
async def viewer(client):
    return await login(client, "viewer@aiecc.local")


@pytest_asyncio.fixture(loop_scope="session")
async def globex(client):
    return await login(client, "admin@globex.local")


async def agent_id(client, headers, name: str) -> str:
    r = await client.get(f"{API}/agents", params={"q": name}, headers=headers)
    return next(a["id"] for a in r.json()["items"] if a["name"] == name)


async def wait_for(fn, timeout: float = 60, interval: float = 0.2):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        v = await fn()
        if v:
            return v
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(interval)
