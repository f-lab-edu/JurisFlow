import hashlib
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.app.core.config import AuthSettings
from backend.app.models.users import RefreshToken, User
from backend.app.schemas.users import (
    SignUpData,
    SignUpRequest,
    TokensResponse,
    UserResponse,
)

password_hasher = PasswordHasher()
seoul_tz = ZoneInfo("Asia/Seoul")

class EmailAlreadyExistsError(Exception):
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

        refresh_token = "rt_" + secrets.token_urlsafe(32)
        session.add(
            RefreshToken(
                token_hash=hashlib.sha256(refresh_token.encode()).hexdigest(),
                user_id=user.user_id,
                created_at=now,
                expires_at=now
                + timedelta(seconds=settings.refresh_token_expire_seconds),
            )
        )
        access_token = jwt.encode(
            {
                "sub": str(user.user_id),
                "iss": settings.jwt_issuer,
                "iat": now,
                "exp": now + timedelta(seconds=settings.access_token_expire_seconds),
                "jti": str(uuid4()),
                "type": "access",
            },
            settings.jwt_secret.get_secret_value(),
            algorithm="HS256",
        )
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
