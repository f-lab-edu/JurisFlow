import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import get_auth_settings
from backend.app.core.config import AuthSettings
from backend.app.core.database import get_db
from backend.app.schemas.auth import (
    RefreshTokenRequest,
    RefreshTokenResponse,
    ResponseMeta,
)
from backend.app.services.auth import InvalidRefreshTokenError
from backend.app.services.auth import refresh_tokens as renew_tokens

router = APIRouter(prefix="/api/auth", tags=["Auth"])
logger = logging.getLogger(__name__)


@router.post(
    "/tokens", response_model=RefreshTokenResponse, status_code=status.HTTP_200_OK
)
async def refresh_tokens(
    request: RefreshTokenRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> RefreshTokenResponse:
    request_id = "req_" + uuid4().hex
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    try:
        data = await renew_tokens(request, session, settings)
    except InvalidRefreshTokenError:
        raise HTTPException(
            401, "유효하지 않거나 만료된 리프레시 토큰입니다.", headers=headers
        ) from None
    except SQLAlchemyError:
        logger.error(
            "Token refresh database operation failed; request_id=%s", request_id
        )
        raise HTTPException(
            503,
            "토큰을 갱신할 수 없습니다. 잠시 후 다시 시도해 주세요.",
            headers=headers,
        ) from None
    response.headers.update(headers)
    return RefreshTokenResponse(data=data, meta=ResponseMeta(request_id=request_id))
