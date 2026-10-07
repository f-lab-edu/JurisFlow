import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.app.core.config import AuthSettings
from backend.app.models.users import RefreshToken, TokenBlacklist, User
from backend.app.schemas.users import (
    SignInRequest,
    SignUpData,
    SignUpRequest,
    TokensResponse,
    UserResponse,
)

password_hasher = PasswordHasher()
DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(32))
seoul_tz = ZoneInfo("Asia/Seoul")


class EmailAlreadyExistsError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class AccountInactiveError(Exception):
    pass


class InvalidAccessTokenError(Exception):
    pass


async def signup(
    request: SignUpRequest, session: AsyncSession, settings: AuthSettings
) -> SignUpData:
    async with session.begin():
        existing = await session.scalar(
            select(User.user_id).where(User.email == request.email)
        )
        if existing is not None:
            raise EmailAlreadyExistsError

        password_hash = await run_in_threadpool(
            password_hasher.hash, request.password.get_secret_value()
        )
        now = datetime.now(seoul_tz)
        user = User(
            user_id=uuid4(),
            email=str(request.email),
            password_hash=password_hash,
            name=request.name,
            status="active",
            created_at=now,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            # The unique constraint also protects concurrent signup requests.
            diagnostic = getattr(exc.orig, "diag", None)
            if getattr(diagnostic, "constraint_name", None) == "uq_users_email":
                raise EmailAlreadyExistsError from exc
            raise

        refresh_token = create_refresh_token(session, user.user_id, settings)
        access_token = create_access_token(settings, str(user.user_id))

        result = SignUpData(
            user=UserResponse(
                user_id=user.user_id,
                email=user.email,
                name=user.name,
                status="active",
                created_at=now,
            ),
            tokens=TokensResponse(
                access_token=access_token,
                expires_in=settings.access_token_expire_seconds,
                refresh_token=refresh_token,
                refresh_token_expires_in=settings.refresh_token_expire_seconds,
            ),
        )
    return result


async def signin(
    request: SignInRequest, session: AsyncSession, settings: AuthSettings
) -> TokensResponse:
    async with session.begin():
        user = await session.scalar(select(User).where(User.email == request.email))
        # Verify a dummy hash as well so unknown emails still incur Argon2 work.
        stored_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        try:
            await run_in_threadpool(
                password_hasher.verify, stored_hash, request.password.get_secret_value()
            )
        except VerificationError, InvalidHashError:
            raise InvalidCredentialsError from None

        if user is None:
            raise InvalidCredentialsError
        if user.status != "active":
            raise AccountInactiveError

        if password_hasher.check_needs_rehash(user.password_hash):
            user.password_hash = await run_in_threadpool(
                password_hasher.hash, request.password.get_secret_value()
            )

        result = TokensResponse(
            access_token=create_access_token(settings, str(user.user_id)),
            expires_in=settings.access_token_expire_seconds,
            refresh_token=create_refresh_token(session, user.user_id, settings),
            refresh_token_expires_in=settings.refresh_token_expire_seconds,
        )

    return result


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
