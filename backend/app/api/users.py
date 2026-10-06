import logging
from functools import lru_cache
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import AuthSettings
from backend.app.core.database import get_db
from backend.app.schemas.users import (
    ResponseMeta,
    SignInRequest,
    SignInResponse,
    SignUpRequest,
    SignUpResponse,
)
from backend.app.services.users import (
    AccountInactiveError,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
)
from backend.app.services.users import signin as authenticate_user
from backend.app.services.users import signup as create_user

router = APIRouter(prefix="/api/users", tags=["Users"])
logger = logging.getLogger(__name__)


@lru_cache
def get_auth_settings() -> AuthSettings:
    try:
        return AuthSettings()
    except ValidationError:
        raise HTTPException(503, "인증 설정을 확인해 주세요.") from None


# 회원가입
@router.post(
    "/signup", response_model=SignUpResponse, status_code=status.HTTP_201_CREATED
)
async def signup(
    request: SignUpRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> SignUpResponse:
    request_id = "req_" + uuid4().hex
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    try:
        data = await create_user(request, session, settings)
    except EmailAlreadyExistsError:
        raise HTTPException(409, "이미 가입된 이메일입니다.", headers=headers) from None
    except SQLAlchemyError:
        logger.error("Signup database operation failed; request_id=%s", request_id)
        raise HTTPException(
            503,
            "회원가입을 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.",
            headers=headers,
        ) from None
    response.headers.update(headers)
    return SignUpResponse(data=data, meta=ResponseMeta(request_id=request_id))


# 로그인
@router.post("/signin", response_model=SignInResponse, status_code=status.HTTP_200_OK)
async def signin(
    request: SignInRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> SignInResponse:
    request_id = "req_" + uuid4().hex
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    try:
        data = await authenticate_user(request, session, settings)
    except InvalidCredentialsError:
        raise HTTPException(
            401, "이메일 또는 비밀번호가 올바르지 않습니다.", headers=headers
        ) from None
    except AccountInactiveError:
        raise HTTPException(403, "비활성화된 계정입니다.", headers=headers) from None
    except SQLAlchemyError:
        logger.error("Signin database operation failed; request_id=%s", request_id)
        raise HTTPException(
            503,
            "로그인을 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.",
            headers=headers,
        ) from None
    response.headers.update(headers)
    return SignInResponse(data=data, meta=ResponseMeta(request_id=request_id))
