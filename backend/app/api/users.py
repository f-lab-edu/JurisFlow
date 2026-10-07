import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import get_access_token, get_auth_settings
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
    InvalidAccessTokenError,
    InvalidCredentialsError,
)
from backend.app.services.users import logout as invalidate_token
from backend.app.services.users import signin as authenticate_user
from backend.app.services.users import signup as create_user

router = APIRouter(prefix="/api/users", tags=["Users"])
logger = logging.getLogger(__name__)


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

# 로그아웃
@router.post("/signout", status_code=status.HTTP_204_NO_CONTENT)
async def signout(
    token: Annotated[str, Depends(get_access_token)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> Response:
    try:
        await invalidate_token(token, session, settings)
    except InvalidAccessTokenError:
        raise HTTPException(
            401,
            "유효하지 않은 인증 토큰입니다.",
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        ) from None
    except SQLAlchemyError:
        logger.error("Signout database operation failed")
        raise HTTPException(
            503,
            "로그아웃을 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.",
            headers={"Cache-Control": "no-store"},
        ) from None
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
