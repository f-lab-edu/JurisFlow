import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import AuthSettings
from backend.app.models.users import RefreshToken, TokenBlacklist

seoul_tz = ZoneInfo("Asia/Seoul")


class InvalidAccessTokenError(Exception):
    pass


def create_refresh_token(session: AsyncSession, user_id: UUID, settings: AuthSettings):
    now = datetime.now(seoul_tz)
    refresh_token = "rt_" + secrets.token_urlsafe(32)

    session.add(
        RefreshToken(
            token_hash=hashlib.sha256(refresh_token.encode()).hexdigest(),
            user_id=user_id,
            created_at=now,
            expires_at=now + timedelta(seconds=settings.refresh_token_expire_seconds),
        )
    )
    return refresh_token


def create_access_token(
    settings: AuthSettings,
    user_id: str,
):
    now = datetime.now(seoul_tz)
    access_token = jwt.encode(
        {
            "sub": str(user_id),
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(seconds=settings.access_token_expire_seconds),
            "jti": str(uuid4()),
            "type": "access",
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return access_token


def decode_access_token(token: str, settings: AuthSettings) -> dict:
    try:
        if len(token) > 2048:
            raise InvalidAccessTokenError
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "iss", "iat", "exp", "jti", "type"]},
        )
        if payload["type"] != "access":
            raise InvalidAccessTokenError
        UUID(payload["sub"])
        datetime.fromtimestamp(payload["exp"], tz=seoul_tz)
        return payload
    except jwt.InvalidTokenError, ValueError, TypeError, OverflowError, OSError:
        raise InvalidAccessTokenError from None


async def validate_access_token(
    token: str, session: AsyncSession, settings: AuthSettings
) -> dict:
    payload = decode_access_token(token, settings)
    if await session.get(TokenBlacklist, token) is not None:
        raise InvalidAccessTokenError
    return payload


async def logout(token: str, session: AsyncSession, settings: AuthSettings) -> None:
    payload = decode_access_token(token, settings)

    try:
        async with session.begin_nested():
            session.add(
                TokenBlacklist(
                    token=token,
                    created_at=datetime.now(seoul_tz),
                    expires_at=datetime.fromtimestamp(payload["exp"], tz=seoul_tz),
                )
            )
            await session.flush()
    except IntegrityError:
        if await session.get(TokenBlacklist, token) is None:
            raise
    await session.commit()
