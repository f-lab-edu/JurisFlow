"""Persistent withdrawal throttling, committed separately from account changes."""

import hashlib
import time
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import bearer, get_auth_settings
from backend.app.core.config import AuthSettings
from backend.app.core.database import get_db
from backend.app.core.exceptions import (
    MissingAccessTokenError,
    WithdrawalRateLimitError,
)
from backend.app.models.users import WithdrawalRateLimit
from backend.app.services.auth import decode_access_token


async def consume_limit(session, identity: str, limit: int, window_seconds: int):
    now = int(time.time())
    start = now - now % window_seconds
    key = hashlib.sha256(identity.encode()).hexdigest()
    insert = sqlite_insert if session.bind.dialect.name == "sqlite" else postgres_insert
    table = WithdrawalRateLimit
    stmt = insert(table).values(key=key, window_start=start, attempts=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.key],
        set_={
            "window_start": start,
            "attempts": case(
                (table.window_start == start, table.attempts + 1), else_=1
            ),
        },
    ).returning(table.attempts)
    async with session.begin():
        attempts = await session.scalar(stmt)
    # Failed password checks must not roll back their rate-limit counters.
    if attempts > limit:
        raise WithdrawalRateLimitError(start + window_seconds - now)


async def get_withdrawal_access_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> str:
    # Use the trusted ASGI client address, never a raw X-Forwarded-For header.
    ip = request.client.host if request.client else "unknown"
    await consume_limit(
        session,
        "ip:" + ip,
        settings.withdrawal_ip_rate_limit,
        settings.withdrawal_window_seconds,
    )
    if credentials is None:
        raise MissingAccessTokenError
    payload = decode_access_token(credentials.credentials, settings)
    await consume_limit(
        session,
        "user:" + payload["sub"],
        settings.withdrawal_rate_limit,
        settings.withdrawal_window_seconds,
    )
    return credentials.credentials
