"""Password hashing (Argon2id), token hashing, JWTs and symmetric encryption of stored credentials."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.core.ids import new_id

_ph = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)  # OWASP 2023 minimums


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


async def hash_password_async(password: str) -> str:
    """Argon2 is deliberately CPU/memory-hard (~50-100 ms): never run it on the event loop."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)


def validate_password_strength(password: str) -> list[str]:
    problems = []
    if len(password) < 12:
        problems.append("must be at least 12 characters")
    if password.lower() == password or password.upper() == password:
        problems.append("must mix upper and lower case")
    if not any(c.isdigit() for c in password):
        problems.append("must contain a digit")
    return problems


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def create_access_token(*, user_id: str, org_id: str, roles: list[str], ttl_s: int | None = None) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "org": org_id,
        "roles": roles,
        "iss": s.jwt_issuer,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_s or s.access_token_ttl_s)).timestamp()),
        "jti": new_id(),
        "typ": "access",
    }
    return jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        payload = jwt.decode(
            token,
            s.jwt_secret.get_secret_value(),
            algorithms=[s.jwt_algorithm],  # explicit allowlist prevents alg=none / confusion attacks
            issuer=s.jwt_issuer,
            options={"require": ["exp", "sub", "org", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid access token") from exc
    if payload.get("typ") != "access":
        raise AuthenticationError("Wrong token type")
    return payload


def _fernet() -> Fernet:
    return Fernet(get_settings().encryption_key.get_secret_value().encode())


def encrypt_secret(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return None


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    return "••••" + value[-4:] if len(value) > 8 else "••••"
