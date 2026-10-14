import secrets
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.app.core.config import AuthSettings
from backend.app.models.users import User
from backend.app.schemas.users import (
    MyProfile,
    SignInRequest,
    SignUpData,
    SignUpRequest,
    TokensResponse,
    UserResponse,
)
from backend.app.services.auth import (
    create_access_token,
    create_refresh_token,
    validate_access_token,
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


class UserNotFoundError(Exception):
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



async def get_my_profile(
    token: str, session: AsyncSession, settings: AuthSettings
) -> MyProfile:
    payload = await validate_access_token(token, session, settings)
    user = await session.get(User, UUID(payload["sub"]))
    if user is None or user.status != "active":
        raise UserNotFoundError

    return MyProfile(
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        status=user.status,
        terms_agreed=user.terms_agreed,
        privacy_agreed=user.privacy_agreed,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
