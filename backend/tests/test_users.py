import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from fastapi import FastAPI
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.app.api.users import get_auth_settings, router
from backend.app.core.config import AuthSettings
from backend.app.core.database import get_db
from backend.app.models.users import Base, RefreshToken, User
from backend.app.schemas.users import SignUpRequest
from backend.app.services.users import EmailAlreadyExistsError, password_hasher, signup

SETTINGS = AuthSettings(_env_file=None, jwt_secret="test-secret-" * 6)
PAYLOAD = {
    "email": "lawyer@example.com",
    "password": "Str0ng!Passw0rd",
    "name": "김법률",
}


def test_signup_persistence_tokens_and_duplicate(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app = FastAPI()
        app.include_router(router)

        async def database():
            async with AsyncSession(engine, expire_on_commit=False) as session:
                yield session

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_auth_settings] = lambda: SETTINGS
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/users/signup", json=PAYLOAD)
                assert response.status_code == 201, response.text
                body = response.json()
                assert set(body) == {"success", "data", "meta"}
                assert body["success"] is True
                user = body["data"]["user"]
                assert set(user) == {"userId", "email", "name", "status", "createdAt"}
                assert user["email"] == PAYLOAD["email"]
                assert user["name"] == PAYLOAD["name"]
                assert user["status"] == "active"
                assert user["createdAt"].endswith("+09:00")
                assert response.headers["X-Request-ID"] == body["meta"]["requestId"]
                assert response.headers["Cache-Control"] == "no-store"
                tokens = body["data"]["tokens"]
                claims = jwt.decode(
                    tokens["accessToken"],
                    SETTINGS.jwt_secret.get_secret_value(),
                    algorithms=["HS256"],
                    issuer="jurisflow",
                )
                assert claims["sub"] == user["userId"]
                assert claims["exp"] - claims["iat"] == tokens["expiresIn"] == 3600
                assert tokens["refreshTokenExpiresIn"] == 2592000
                assert tokens["tokenType"] == "Bearer"
                async with AsyncSession(engine) as session:
                    stored = await session.scalar(select(User))
                    assert password_hasher.verify(
                        stored.password_hash, PAYLOAD["password"]
                    )
                    refresh = await session.scalar(select(RefreshToken))
                    assert (
                        refresh.token_hash
                        == hashlib.sha256(tokens["refreshToken"].encode()).hexdigest()
                    )
                    assert refresh.user_id == stored.user_id
                    assert (
                        refresh.expires_at - refresh.created_at
                    ).total_seconds() == 2592000
                duplicate = await client.post(
                    "/api/users/signup", json={**PAYLOAD, "email": "LAWYER@EXAMPLE.COM"}
                )
                assert duplicate.status_code == 409
                for changes in (
                    {"email": "invalid"},
                    {"password": "short"},
                    {"name": "   "},
                    {"password": "x" * 129},
                    {"name": "x" * 101},
                ):
                    invalid = await client.post(
                        "/api/users/signup", json={**PAYLOAD, **changes}
                    )
                    assert invalid.status_code == 422
                assert (
                    await client.post("/api/users/signup", params=PAYLOAD)
                ).status_code == 422

                def fail_refresh_insert(
                    conn, cursor, statement, parameters, context, many
                ):
                    if statement.startswith("INSERT INTO user_refresh_tokens"):
                        raise IntegrityError(statement, None, Exception("write failed"))

                event.listen(
                    engine.sync_engine, "before_cursor_execute", fail_refresh_insert
                )
                failed = await client.post(
                    "/api/users/signup", json={**PAYLOAD, "email": "second@example.com"}
                )
                assert failed.status_code == 503
                async with AsyncSession(engine) as session:
                    assert (
                        await session.scalar(select(func.count()).select_from(User))
                        == 1
                    )
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(RefreshToken)
                        )
                        == 1
                    )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_concurrent_duplicate_constraint_translation():
    async def run():
        session = AsyncMock(spec=AsyncSession)
        session.begin.return_value.__aenter__ = AsyncMock()
        session.scalar.return_value = None
        error = Exception("duplicate")
        error.diag = SimpleNamespace(constraint_name="uq_users_email")
        session.flush.side_effect = IntegrityError("insert", None, error)
        with (
            patch("backend.app.services.users.run_in_threadpool", return_value="hash"),
            pytest.raises(EmailAlreadyExistsError),
        ):
            await signup(SignUpRequest(**PAYLOAD), session, SETTINGS)
        assert (
            session.begin.return_value.__aexit__.call_args.args[0]
            is EmailAlreadyExistsError
        )

    asyncio.run(run())


def test_signin_response_failures_and_transaction(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'signin.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app = FastAPI()
        app.include_router(router)

        async def database():
            async with AsyncSession(engine, expire_on_commit=False) as session:
                yield session

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_auth_settings] = lambda: SETTINGS
        credentials = {"email": PAYLOAD["email"], "password": PAYLOAD["password"]}
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                signup_response = await client.post("/api/users/signup", json=PAYLOAD)
                assert signup_response.status_code == 201
                user_id = signup_response.json()["data"]["user"]["userId"]
                issued_tokens = []
                for _ in range(2):
                    response = await client.post(
                        "/api/users/signin",
                        json={**credentials, "email": PAYLOAD["email"].upper()},
                    )
                    assert response.status_code == 200, response.text
                    body = response.json()
                    assert set(body) == {"success", "data", "meta"}
                    assert body["success"] is True
                    assert set(body["meta"]) == {"requestId"}
                    assert body["meta"]["requestId"].startswith("req_")
                    assert response.headers["X-Request-ID"] == body["meta"]["requestId"]
                    assert response.headers["Cache-Control"] == "no-store"
                    tokens = body["data"]
                    assert set(tokens) == {
                        "accessToken",
                        "tokenType",
                        "expiresIn",
                        "refreshToken",
                        "refreshTokenExpiresIn",
                    }
                    assert tokens["tokenType"] == "Bearer"
                    assert tokens["expiresIn"] == 3600
                    assert tokens["refreshTokenExpiresIn"] == 2592000
                    assert tokens["refreshToken"].startswith("rt_")
                    claims = jwt.decode(
                        tokens["accessToken"],
                        SETTINGS.jwt_secret.get_secret_value(),
                        algorithms=["HS256"],
                        issuer=SETTINGS.jwt_issuer,
                    )
                    assert claims["sub"] == user_id
                    assert claims["type"] == "access"
                    assert claims["exp"] - claims["iat"] == 3600
                    async with AsyncSession(engine) as session:
                        stored = await session.get(
                            RefreshToken,
                            hashlib.sha256(tokens["refreshToken"].encode()).hexdigest(),
                        )
                        assert str(stored.user_id) == user_id
                        assert (
                            stored.expires_at - stored.created_at
                        ).total_seconds() == 2592000
                    issued_tokens.append(tokens)
                assert (
                    issued_tokens[0]["accessToken"] != issued_tokens[1]["accessToken"]
                )
                assert (
                    issued_tokens[0]["refreshToken"] != issued_tokens[1]["refreshToken"]
                )

                for changes in (
                    {"email": "missing@example.com"},
                    {"password": "WrongPassword!"},
                ):
                    failed = await client.post(
                        "/api/users/signin", json={**credentials, **changes}
                    )
                    assert failed.status_code == 401
                    assert failed.json() == {
                        "detail": "이메일 또는 비밀번호가 올바르지 않습니다."
                    }
                    assert failed.headers["Cache-Control"] == "no-store"
                    assert "accessToken" not in failed.text
                for invalid in (
                    {},
                    {**credentials, "email": "invalid"},
                    {**credentials, "password": "x" * 129},
                ):
                    assert (
                        await client.post("/api/users/signin", json=invalid)
                    ).status_code == 422

                async with AsyncSession(engine) as session, session.begin():
                    user = await session.scalar(select(User))
                    user.status = "inactive"
                assert (
                    await client.post("/api/users/signin", json=credentials)
                ).status_code == 403
                async with AsyncSession(engine) as session, session.begin():
                    user = await session.scalar(select(User))
                    user.status = "active"

                def fail_refresh_insert(
                    conn, cursor, statement, parameters, context, many
                ):
                    if statement.startswith("INSERT INTO user_refresh_tokens"):
                        raise IntegrityError(statement, None, Exception("write failed"))

                event.listen(
                    engine.sync_engine, "before_cursor_execute", fail_refresh_insert
                )
                failed = await client.post("/api/users/signin", json=credentials)
                assert failed.status_code == 503
                assert "accessToken" not in failed.text
                async with AsyncSession(engine) as session:
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(RefreshToken)
                        )
                        == 3
                    )
                event.remove(
                    engine.sync_engine, "before_cursor_execute", fail_refresh_insert
                )
                async with AsyncSession(engine) as session, session.begin():
                    user = await session.scalar(select(User))
                    user.password_hash = "invalid-hash"
                assert (
                    await client.post("/api/users/signin", json=credentials)
                ).status_code == 401
        finally:
            await engine.dispose()

    asyncio.run(run())
