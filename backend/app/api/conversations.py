import asyncio
import json
from datetime import UTC
from functools import lru_cache
from typing import Annotated
from uuid import UUID, uuid4

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.rag import get_service
from backend.app.core.auth import get_access_token, get_auth_settings
from backend.app.core.config import AuthSettings, ChatSettings
from backend.app.core.database import get_db
from backend.app.schemas.chat import ConversationListResponse, MessageRequest
from backend.app.services.auth import decode_access_token
from backend.app.services.chat_agent import ChatAgent
from backend.app.services.conversations import (
    ConversationError,
    complete_message,
    fail_message,
    list_conversations,
    reserve_message,
)
from backend.app.services.llm import ContextTooLong, LLMError, VLLMClient

ERROR_CODES = {
    400: "INVALID_REQUEST",
    401: "INVALID_TOKEN",
    403: "FORBIDDEN",
    404: "CONVERSATION_NOT_FOUND",
    429: "RATE_LIMIT_EXCEEDED",
    409: "GENERATION_IN_PROGRESS",
    422: "VALIDATION_ERROR",
    503: "LLM_SERVICE_UNAVAILABLE",
}


def error_body(code, message, request_id, details=None):
    return {
        "success": False,
        "error": {"code": code, "message": message, "details": details or []},
        "meta": {"requestId": request_id},
    }


class ChatRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            request_id = "req_" + uuid4().hex
            supplied = request.headers.get("X-Request-Id")
            if supplied:
                try:
                    request_id = str(UUID(supplied))
                except ValueError:
                    return JSONResponse(
                        error_body(
                            "INVALID_REQUEST",
                            "X-Request-Id는 UUID여야 합니다.",
                            request_id,
                        ),
                        status_code=400,
                    )
            request.state.request_id = request_id
            headers = {"X-Request-Id": request_id, "Cache-Control": "no-store"}
            try:
                response = await handler(request)
                response.headers["X-Request-Id"] = request_id
                return response
            except RequestValidationError as exc:
                details = [
                    {"field": ".".join(map(str, e["loc"])), "reason": e["msg"]}
                    for e in exc.errors()
                ]
                return JSONResponse(
                    error_body(
                        "VALIDATION_ERROR",
                        "요청값을 확인해 주세요.",
                        request_id,
                        details,
                    ),
                    status_code=422,
                    headers=headers,
                )
            except HTTPException as exc:
                headers.update(exc.headers or {})
                return JSONResponse(
                    error_body(
                        ERROR_CODES.get(exc.status_code, "INTERNAL_SERVER_ERROR"),
                        str(exc.detail),
                        request_id,
                    ),
                    status_code=exc.status_code,
                    headers=headers,
                )

        return wrapped


router = APIRouter(
    prefix="/api/conversations", tags=["Conversations"], route_class=ChatRoute
)
Session = Annotated[AsyncSession, Depends(get_db)]


# 인증된 토큰에서 사용자 ID 가져오기
async def current_user(
    token: Annotated[str, Depends(get_access_token)],
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
):
    return UUID(decode_access_token(token, settings)["sub"])


@lru_cache
def get_chat_settings():
    return ChatSettings()


# vLLM과 검색 서비스를 연결한 채팅 에이전트
async def get_agent(request: Request):
    try:
        settings = get_chat_settings()
        checkpointer = getattr(request.app.state, "chat_checkpointer", None)
        if checkpointer is None:
            raise HTTPException(503, "체크포인트 저장소가 준비되지 않았습니다.")
        return ChatAgent(
            VLLMClient(settings), get_service(), settings, checkpointer=checkpointer
        )
    except ValidationError, ValueError:
        raise HTTPException(503, "채팅 설정을 확인해 주세요.") from None


# 이벤트 데이터를 SSE 전송 형식으로 변환
def event(name, data):
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


# 메시지를 저장하고 답변을 SSE로 전송
async def stream_response(
    body, session, user_id, key, agent, request, conversation_id=None
):
    if key is None:
        raise HTTPException(400, "Idempotency-Key(UUID) 헤더가 필요합니다.")
    try:
        reserved = await reserve_message(session, user_id, body, key, conversation_id)
    except ConversationError as exc:
        await session.rollback()
        raise HTTPException(exc.status, exc.detail) from None
    except SQLAlchemyError:
        await session.rollback()
        raise HTTPException(503, "채팅 저장소를 사용할 수 없습니다.") from None
    answer = reserved["answer"]
    answer_id = str(answer.message_id)
    start = {
        "conversationId": str(answer.conversation_id),
        "userMessageId": str(reserved["question"].message_id),
        "assistantMessageId": answer_id,
        "status": "streaming",
        "createdAt": answer.created_at.replace(tzinfo=UTC)
        .isoformat()
        .replace("+00:00", "Z"),
    }

    async def stream():
        work = None
        try:
            yield event("message_start", start)
            if not reserved["replay"]:
                work = asyncio.create_task(complete_message(session, reserved, agent))
                while not work.done():
                    done, _ = await asyncio.wait({work}, timeout=10)
                    if not done:
                        yield ": keep-alive\n\n"
                final = await work
            else:
                final = answer
            for offset in range(0, len(final.content), 40):
                yield event(
                    "content_delta",
                    {
                        "messageId": answer_id,
                        "delta": final.content[offset : offset + 40],
                    },
                )
                await asyncio.sleep(0)
            yield event(
                "message_complete",
                {
                    "messageId": answer_id,
                    "status": "completed",
                    "finishReason": final.finish_reason,
                    "usage": final.usage,
                },
            )
        except LLMError, TimeoutError:
            yield event(
                "error",
                error_body(
                    "LLM_SERVICE_UNAVAILABLE",
                    "모델 서버 응답을 받지 못했습니다.",
                    request.state.request_id,
                ),
            )
        except ContextTooLong as exc:
            yield event(
                "error",
                error_body("VALIDATION_ERROR", str(exc), request.state.request_id),
            )
        except Exception:  # noqa: BLE001 - finish SSE without exposing internal errors
            yield event(
                "error",
                error_body(
                    "INTERNAL_SERVER_ERROR",
                    "답변 생성 중 오류가 발생했습니다.",
                    request.state.request_id,
                ),
            )
        finally:
            with anyio.CancelScope(shield=True):
                if work is not None and not work.done():
                    work.cancel()
                    try:
                        await work
                    except asyncio.CancelledError:
                        pass
                elif work is None and not reserved["replay"]:
                    await fail_message(session, answer.message_id)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# 채팅방을 자동 생성하고 첫 답변을 스트리밍
@router.post("/messages", response_class=StreamingResponse)
async def new_message(
    body: MessageRequest,
    session: Session,
    request: Request,
    user_id: Annotated[UUID, Depends(current_user)],
    agent: Annotated[ChatAgent, Depends(get_agent)],
    key: Annotated[UUID | None, Header(alias="Idempotency-Key")] = None,
):
    return await stream_response(body, session, user_id, key, agent, request)


# 기존 채팅방에서 후속 답변을 스트리밍
@router.post("/{conversation_id}/messages", response_class=StreamingResponse)
async def existing_message(
    conversation_id: UUID,
    body: MessageRequest,
    session: Session,
    request: Request,
    user_id: Annotated[UUID, Depends(current_user)],
    agent: Annotated[ChatAgent, Depends(get_agent)],
    key: Annotated[UUID | None, Header(alias="Idempotency-Key")] = None,
):
    return await stream_response(
        body, session, user_id, key, agent, request, conversation_id
    )


# 채팅방 목록을 최근 수정순으로 조회
@router.get("", response_model=ConversationListResponse)
async def get_conversations(
    session: Session,
    request: Request,
    user_id: Annotated[UUID, Depends(current_user)],
    limit: Annotated[int, Query()] = 20,
    cursor: Annotated[str | None, Query()] = None,
):
    if not 1 <= limit <= 100:
        raise HTTPException(400, "limit은 1~100 사이여야 합니다.")
    try:
        data = await list_conversations(session, user_id, limit, cursor)
    except ConversationError as exc:
        raise HTTPException(exc.status, exc.detail) from None
    except SQLAlchemyError:
        raise HTTPException(503, "잠시 후 다시 시도해 주세요.") from None
    return {
        "success": True,
        "data": data,
        "meta": {"requestId": request.state.request_id},
    }
