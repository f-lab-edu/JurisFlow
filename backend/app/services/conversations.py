import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from backend.app.models.conversations import Conversation, ConversationRun, Message
from backend.app.models.users import User


class ConversationError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(detail)


# 체크포인트가 없는 기존 채팅방의 최근 3턴을 최초 한 번 불러오기
async def initial_history(session, user_id, conversation_id):
    question = aliased(Message)
    rows = (
        await session.execute(
            select(question.content, Message.content)
            .join(
                question,
                Message.parent_message_id == question.message_id,
            )
            .where(
                Message.user_id == user_id,
                question.user_id == user_id,
                Message.conversation_id == conversation_id,
                question.conversation_id == conversation_id,
                Message.role == "assistant",
                Message.status == "completed",
                question.role == "user",
                question.status == "completed",
            )
            .order_by(Message.created_at.desc(), Message.message_id.desc())
            .limit(3)
        )
    ).all()
    return [
        message
        for q, a in reversed(rows)
        for message in (
            {"role": "user", "content": q},
            {"role": "assistant", "content": a},
        )
    ]


# 사용자 소유의 삭제되지 않은 채팅방인지 확인
async def owned_conversation(session, user_id, conversation_id, *, lock=False):
    statement = select(Conversation).where(
        Conversation.conversation_id == conversation_id,
        Conversation.user_id == user_id,
        Conversation.deleted_at.is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    room = await session.scalar(statement)
    if room is None:
        raise ConversationError(404, "채팅방을 찾을 수 없습니다.")
    return room


# 대화를 요청한 사용자가 활성 계정인지 확인
async def active_user(session, user_id):
    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise ConversationError(403, "비활성화된 계정입니다.")


# 동일 요청의 완료 결과를 재사용하거나 실패한 실행 예약을 다시 확보
async def replay(session, existing, fingerprint):
    if existing.request_hash != fingerprint:
        raise ConversationError(
            409, "동일한 Idempotency-Key에 다른 요청을 사용할 수 없습니다."
        )
    await owned_conversation(
        session, existing.user_id, existing.conversation_id, lock=True
    )
    answer = await session.scalar(
        select(Message)
        .where(
            Message.parent_message_id == existing.message_id,
            Message.role == "assistant",
            Message.user_id == existing.user_id,
        )
        .execution_options(populate_existing=True)
    )
    if answer is None:
        raise ConversationError(409, "기존 요청의 답변 기록을 확인할 수 없습니다.")
    if answer.status == "completed":
        return {"question": existing, "answer": answer, "replay": True}
    run = await session.get(ConversationRun, answer.message_id, populate_existing=True)
    if run is None:
        raise ConversationError(
            409, "체크포인트 도입 이전에 실패한 요청입니다. 새 키로 요청하세요."
        )
    updated = answer.updated_at.replace(tzinfo=UTC)
    expired = updated < datetime.now(UTC) - timedelta(seconds=630)
    if answer.status == "streaming" and not expired:
        raise ConversationError(409, "이 요청의 답변을 생성 중입니다.")
    answer.status = "streaming"
    answer.finish_reason = None
    answer.updated_at = datetime.now(UTC)
    run.status = "running"
    run.updated_at = answer.updated_at
    await session.commit()
    return {"question": existing, "answer": answer, "run": run, "replay": False}


# 중복 요청을 확인하고 사용자 메시지와 답변 생성 예약을 저장
async def reserve_message(
    session, user_id: UUID, request, key: UUID, conversation_id=None
):
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "conversation_id": str(conversation_id),
                "content": request.content,
                "parent": str(request.parent_message_id),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    await active_user(session, user_id)
    existing = await session.scalar(
        select(Message).where(
            Message.user_id == user_id,
            Message.idempotency_key == key,
        )
    )
    if existing:
        return await replay(session, existing, fingerprint)
    if conversation_id is None:
        if request.parent_message_id is not None:
            raise ConversationError(
                400, "새 채팅에는 parentMessageId를 지정할 수 없습니다."
            )
        now = datetime.now(UTC)
        room = Conversation(
            conversation_id=uuid4(),
            user_id=user_id,
            title=request.content[:100],
            created_at=now,
            updated_at=now,
        )
        session.add(room)
        await session.flush()
    else:
        room = await owned_conversation(session, user_id, conversation_id, lock=True)
    room_id = room.conversation_id
    unresolved = await session.scalar(
        select(ConversationRun).where(
            ConversationRun.conversation_id == room_id,
            ConversationRun.status != "completed",
        )
    )
    if unresolved is not None:
        raise ConversationError(
            409, "미완료 요청이 있습니다. 기존 Idempotency-Key로 먼저 재시도하세요."
        )
    # 이전 버전에서 남은 생성 예약만 만료. 새 실행은 같은 키로 복구
    await session.execute(
        update(Message)
        .where(
            Message.conversation_id == room_id,
            Message.status == "streaming",
            Message.updated_at < datetime.now(UTC) - timedelta(seconds=630),
        )
        .values(status="failed", finish_reason="error", updated_at=datetime.now(UTC))
    )
    if request.parent_message_id:
        parent = await session.get(Message, request.parent_message_id)
        if (
            parent is None
            or parent.user_id != user_id
            or parent.conversation_id != room_id
            or parent.role != "assistant"
            or parent.status != "completed"
        ):
            raise ConversationError(400, "유효하지 않은 parentMessageId입니다.")
    now = datetime.now(UTC)
    question = Message(
        message_id=uuid4(),
        conversation_id=room_id,
        user_id=user_id,
        role="user",
        content=request.content,
        status="completed",
        parent_message_id=request.parent_message_id,
        idempotency_key=key,
        request_hash=fingerprint,
        created_at=now,
        updated_at=now,
    )
    answer = Message(
        message_id=uuid4(),
        conversation_id=room_id,
        user_id=user_id,
        role="assistant",
        content="",
        status="streaming",
        parent_message_id=question.message_id,
        created_at=now,
        updated_at=now,
    )
    try:
        session.add(question)
        await session.flush()
        session.add(answer)
        await session.flush()
        run = ConversationRun(
            assistant_message_id=answer.message_id,
            conversation_id=room_id,
            thread_id=f"{user_id}:{room_id}",
            status="running",
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(Message).where(
                Message.user_id == user_id,
                Message.idempotency_key == key,
            )
        )
        if existing:
            return await replay(session, existing, fingerprint)
        raise ConversationError(
            409, "이 채팅방에서 다른 답변을 생성 중입니다."
        ) from None
    return {"question": question, "answer": answer, "run": run, "replay": False}


# 에이전트를 실행하고 최종 답변과 사용량을 저장
async def complete_message(session, reserved, agent):
    answer = reserved["answer"]
    answer_id = answer.message_id
    try:
        run = await session.get(ConversationRun, answer_id)
        async with asyncio.timeout(agent.settings.request_timeout):
            history = []
            if not await agent.has_memory(run.thread_id):
                history = await initial_history(
                    session, answer.user_id, answer.conversation_id
                )
            result = await agent.run(
                reserved["question"].content,
                history,
                thread_id=run.thread_id,
                run_id=str(answer_id),
            )
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError("Empty final response")
        answer.content = result
        answer.status = "completed"
        answer.finish_reason = "stop"
        answer.updated_at = datetime.now(UTC)
        answer.usage = getattr(agent.llm, "usage", None)
        room = await session.get(Conversation, answer.conversation_id)
        room.updated_at = answer.updated_at
        run.status = "completed"
        run.updated_at = answer.updated_at
        await session.commit()
    except BaseException:
        await session.rollback()
        await asyncio.shield(fail_message(session, answer_id))
        raise
    return answer


# 실패한 답변의 생성 예약을 해제
async def fail_message(session, answer_id):
    stored = await session.get(Message, answer_id)
    if stored is not None and stored.status == "streaming":
        stored.status = "failed"
        stored.finish_reason = "error"
        stored.updated_at = datetime.now(UTC)
        run = await session.get(ConversationRun, answer_id)
        if run is not None:
            run.status = "retryable"
            run.updated_at = stored.updated_at
        await session.commit()


# 사용자 소유 채팅방의 제목을 수정
async def update_title(session, user_id, conversation_id, title):
    await active_user(session, user_id)
    room = await owned_conversation(session, user_id, conversation_id, lock=True)
    room.title = title
    room.updated_at = datetime.now(UTC)
    await session.commit()
    return {
        "conversation_id": room.conversation_id,
        "title": room.title,
        "updated_at": room.updated_at,
    }


# 사용자 소유 채팅방을 소프트 삭제. 답변 생성 중이면 거부
async def delete_conversation(session, user_id, conversation_id):
    await active_user(session, user_id)
    room = await owned_conversation(session, user_id, conversation_id, lock=True)
    generating = await session.scalar(
        select(ConversationRun.assistant_message_id)
        .where(
            ConversationRun.conversation_id == room.conversation_id,
            ConversationRun.status != "completed",
        )
        .union(
            select(Message.message_id).where(
                Message.conversation_id == room.conversation_id,
                Message.status == "streaming",
            )
        )
        .limit(1)
    )
    if generating is not None:
        raise ConversationError(409, "답변 생성 중에는 삭제할 수 없습니다.")
    room.deleted_at = datetime.now(UTC)
    await session.commit()


# 커서는 (updated_at, conversation_id)를 담은 불투명 문자열
def encode_cursor(room):
    raw = json.dumps([room.updated_at.isoformat(), str(room.conversation_id)])
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor):
    try:
        updated_at, conversation_id = json.loads(base64.urlsafe_b64decode(cursor))
        updated_at = datetime.fromisoformat(updated_at)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return updated_at, UUID(conversation_id)
    except ValueError, TypeError:
        raise ConversationError(400, "cursor가 올바르지 않습니다.") from None


# 최근 수정순으로 채팅방 목록을 커서 기반으로 조회
async def list_conversations(session, user_id, limit, cursor=None):
    statement = select(Conversation).where(
        Conversation.user_id == user_id, Conversation.deleted_at.is_(None)
    )
    if cursor:
        statement = statement.where(
            tuple_(Conversation.updated_at, Conversation.conversation_id)
            < tuple_(*decode_cursor(cursor))
        )
    rooms = list(
        await session.scalars(
            statement.order_by(
                Conversation.updated_at.desc(), Conversation.conversation_id.desc()
            ).limit(limit + 1)
        )
    )
    has_next = len(rooms) > limit
    rooms = rooms[:limit]

    last_messages = {}
    if rooms:
        ranked = (
            select(
                Message.conversation_id,
                Message.role,
                Message.content,
                Message.created_at,
                func.row_number()
                .over(
                    partition_by=Message.conversation_id,
                    order_by=(Message.created_at.desc(), Message.message_id.desc()),
                )
                .label("rank"),
            )
            .where(Message.conversation_id.in_([r.conversation_id for r in rooms]))
            .subquery()
        )
        rows = await session.execute(select(ranked).where(ranked.c.rank == 1))
        last_messages = {
            row.conversation_id: {
                "role": row.role,
                "content_preview": row.content[:100],
                "created_at": row.created_at,
            }
            for row in rows
        }

    return {
        "items": [
            {
                "conversation_id": room.conversation_id,
                "title": room.title,
                "last_message": last_messages.get(room.conversation_id),
                "created_at": room.created_at,
                "updated_at": room.updated_at,
            }
            for room in rooms
        ],
        "page": {
            "next_cursor": encode_cursor(rooms[-1]) if has_next else None,
            "has_next": has_next,
            "limit": limit,
        },
    }
