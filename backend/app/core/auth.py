from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import AuthSettings
from backend.app.core.database import get_db
from backend.app.services.users import InvalidAccessTokenError, validate_access_token

bearer = HTTPBearer(auto_error=False)


@lru_cache
def get_auth_settings() -> AuthSettings:
    try:
        return AuthSettings()
    except ValidationError:
        raise HTTPException(503, "인증 설정을 확인해 주세요.") from None


async def get_access_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> str:
    """인증이 필요한 API에서 사용하는 DB 블랙리스트 검사 의존성."""
    headers = {"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"}
    if credentials is None:
        raise HTTPException(401, "인증 토큰이 필요합니다.", headers=headers)
    try:
        await validate_access_token(credentials.credentials, session, settings)
    except InvalidAccessTokenError:
        raise HTTPException(
            401, "유효하지 않은 인증 토큰입니다.", headers=headers
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            503, "인증 정보를 확인할 수 없습니다.", headers=headers
        ) from None
    return credentials.credentials
