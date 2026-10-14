import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from backend.app.core.exceptions import MissingAccessTokenError, UserNotFoundError
from backend.app.services.auth import ExpiredAccessTokenError, InvalidAccessTokenError

logger = logging.getLogger(__name__)


def is_profile_update(request: Request) -> bool:
    route = request.scope.get("route")
    return (
        request.method == "PATCH"
        and route is not None
        and route.name == "update_my_profile"
    )


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else {}
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "success": False,
            "error": {"code": code, "message": message, "details": details or []},
            "meta": {"requestId": request.state.request_id},
        },
    )


async def users_validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    if not is_profile_update(request):
        return await request_validation_exception_handler(request, exc)
    errors = exc.errors()
    malformed_json = any(e["type"] == "json_invalid" for e in errors)
    return error_response(
        request,
        400 if malformed_json else 422,
        "INVALID_REQUEST" if malformed_json else "VALIDATION_ERROR",
        "요청값이 올바르지 않습니다.",
        [
            {"field": ".".join(str(part) for part in e["loc"][1:]), "reason": e["msg"]}
            for e in errors
        ],
    )


async def profile_exception_handler(request: Request, exc: Exception):
    # Other endpoints retain their existing local exception handling.
    if not is_profile_update(request):
        raise exc
    if isinstance(exc, MissingAccessTokenError):
        return error_response(request, 401, "INVALID_TOKEN", "인증 토큰이 필요합니다.")
    if isinstance(exc, ExpiredAccessTokenError):
        return error_response(
            request, 401, "TOKEN_EXPIRED", "인증 토큰이 만료되었습니다."
        )
    if isinstance(exc, InvalidAccessTokenError):
        return error_response(
            request, 401, "INVALID_TOKEN", "유효하지 않은 인증 토큰입니다."
        )
    if isinstance(exc, UserNotFoundError):
        return error_response(
            request, 404, "USER_NOT_FOUND", "회원정보를 찾을 수 없습니다."
        )
    logger.error("Profile update failed; request_id=%s", request.state.request_id)
    return error_response(
        request, 503, "SERVICE_UNAVAILABLE", "회원정보를 수정할 수 없습니다."
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        RequestValidationError, users_validation_exception_handler
    )
    for exception in (
        MissingAccessTokenError,
        InvalidAccessTokenError,
        UserNotFoundError,
        SQLAlchemyError,
    ):
        app.add_exception_handler(exception, profile_exception_handler)
