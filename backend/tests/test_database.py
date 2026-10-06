import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.core.config import DatabaseSettings
from backend.app.core.database import create_database_engine, get_db


def test_engine_preserves_password_and_verifies_tls(tmp_path):
    cert = tmp_path / "ca.pem"
    cert.write_text("test certificate placeholder")
    password = "p@ss:/?#%word"
    settings = DatabaseSettings(
        _env_file=None,
        host="test.rds.amazonaws.com",
        user="postgres",
        password=password,
        sslrootcert=cert,
    )
    with patch("backend.app.core.database.create_async_engine") as create:
        create_database_engine(settings)
    url = create.call_args.args[0]
    assert url.password == password
    assert password not in repr(settings)
    args = create.call_args.kwargs
    assert args["connect_args"]["sslmode"] == "verify-full"
    assert args["connect_args"]["sslrootcert"] == str(cert)
    assert args["pool_pre_ping"] is True


def test_missing_certificate_fails_before_connecting(tmp_path):
    settings = DatabaseSettings(
        _env_file=None,
        host="test.rds.amazonaws.com",
        user="postgres",
        password="test",
        sslrootcert=tmp_path / "missing.pem",
    )
    with pytest.raises(ValueError, match="DB_SSLROOTCERT"):
        create_database_engine(settings)


def test_session_context_closes_on_request_failure():
    async def run():
        session = AsyncMock()
        session.__aenter__.return_value = session
        with (
            patch("backend.app.core.database.get_engine"),
            patch("backend.app.core.database.AsyncSession", return_value=session),
        ):
            dependency = get_db()
            assert await anext(dependency) is session
            with pytest.raises(RuntimeError, match="request failed"):
                await dependency.athrow(RuntimeError("request failed"))
            session.__aexit__.assert_awaited_once()
            session.commit.assert_not_awaited()

    asyncio.run(run())
