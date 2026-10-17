import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.api.conversations import current_user
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models.conversations import Conversation, Message
from backend.app.models.users import Base

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def room(user_id, minutes, title="제목", deleted=False):
    at = NOW + timedelta(minutes=minutes)
    return Conversation(
        conversation_id=uuid4(),
        user_id=user_id,
        title=title,
        created_at=at,
        updated_at=at,
        deleted_at=at if deleted else None,
    )


def message(conversation, role, content, minutes):
    at = NOW + timedelta(minutes=minutes)
    return Message(
        message_id=uuid4(),
        conversation_id=conversation.conversation_id,
        user_id=conversation.user_id,
        role=role,
        content=content,
        status="completed",
        created_at=at,
        updated_at=at,
    )


def run(seed, scenario):
    async def main():
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        me, other = uuid4(), uuid4()
        async with factory() as session:
            session.add_all(seed(me, other))
            await session.commit()

        async def override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[current_user] = lambda: me
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await scenario(client)
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    return asyncio.run(main())


def test_lists_recent_first_and_excludes_deleted_and_others():
    def seed(me, other):
        old, new = room(me, 1, "old"), room(me, 5, "new")
        return [
            old,
            new,
            room(me, 9, "gone", deleted=True),
            room(other, 7, "other"),
            message(old, "user", "질문", 1),
            message(old, "assistant", "답" * 150, 2),
        ]

    async def scenario(client):
        return await client.get("/api/conversations")

    response = run(seed, scenario)
    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert [i["title"] for i in body["data"]["items"]] == ["new", "old"]
    new, old = body["data"]["items"]
    assert new["lastMessage"] is None
    assert old["lastMessage"]["role"] == "assistant"
    assert old["lastMessage"]["contentPreview"] == "답" * 100
    assert old["lastMessage"]["createdAt"].endswith("Z")
    assert body["data"]["page"] == {"nextCursor": None, "hasNext": False, "limit": 20}
    assert UUID(old["conversationId"])


def test_cursor_pagination_has_no_duplicates_or_gaps():
    def seed(me, other):
        # 같은 updated_at의 방도 conversation_id로 안정적으로 이어져야 한다.
        return [room(me, m // 2, f"r{m}") for m in range(7)]

    async def scenario(client):
        seen, cursor = [], None
        for _ in range(10):
            params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
            body = (await client.get("/api/conversations", params=params)).json()
            seen += [i["conversationId"] for i in body["data"]["items"]]
            page = body["data"]["page"]
            if not page["hasNext"]:
                break
            cursor = page["nextCursor"]
        return seen

    seen = run(seed, scenario)
    assert len(seen) == 7
    assert len(set(seen)) == 7


def test_invalid_limit_and_cursor_return_400():
    async def scenario(client):
        return [
            (await client.get("/api/conversations", params=params)).status_code
            for params in (
                {"limit": 0},
                {"limit": 101},
                {"cursor": "not-a-cursor"},
            )
        ]

    assert run(lambda me, other: [], scenario) == [400, 400, 400]


def test_requires_authentication():
    async def scenario(client):
        app.dependency_overrides.pop(current_user)
        return (await client.get("/api/conversations")).status_code

    assert run(lambda me, other: [], scenario) == 401
