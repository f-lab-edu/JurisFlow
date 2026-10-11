import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo

from backend.app.core.config import DatabaseSettings


# 기존 DB 설정과 RDS 인증서를 사용해 별도의 체크포인트 연결
def checkpoint_conninfo(settings: DatabaseSettings) -> str:
    if not settings.sslrootcert.is_file():
        raise ValueError("RDS CA certificate missing; configure DB_SSLROOTCERT.")
    return make_conninfo(
        host=settings.host,
        port=settings.port,
        dbname=settings.name,
        user=settings.user,
        password=settings.password.get_secret_value(),
        sslmode="verify-full",
        sslrootcert=str(settings.sslrootcert),
        connect_timeout=settings.connect_timeout,
    )


# 앱 실행 동안 연결을 유지하며 누락된 체크포인트 테이블 확인
@asynccontextmanager
async def open_checkpointer():
    async with AsyncExitStack() as stack:
        try:
            saver = await stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(
                    checkpoint_conninfo(DatabaseSettings())
                )
            )
            async with saver.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT to_regclass('checkpoints') AS checkpoints, "
                    "to_regclass('checkpoint_writes') AS writes, "
                    "to_regclass('checkpoint_blobs') AS blobs, "
                    "to_regclass('conversation_runs') AS runs"
                )
                tables = await cursor.fetchone()
                if not all(tables.values()):
                    raise RuntimeError("Checkpoint schema is not initialized")
        except Exception:
            raise RuntimeError(
                "채팅 DB 연결·인증서·스키마를 확인하세요. 초기화 명령: alembic upgrade head, "
                "python -m backend.app.core.checkpoints"
            ) from None
        yield saver


# 배포 단계에서 체크포인트 전용 테이블과 라이브러리 마이그레이션 적용
async def setup_checkpoints():
    async with AsyncPostgresSaver.from_conn_string(
        checkpoint_conninfo(DatabaseSettings())
    ) as saver:
        async with saver.conn.cursor() as cursor:
            await cursor.execute("SELECT pg_advisory_lock(784203104)")
        try:
            await saver.setup()
        finally:
            async with saver.conn.cursor() as cursor:
                await cursor.execute("SELECT pg_advisory_unlock(784203104)")


if __name__ == "__main__":
    try:
        asyncio.run(setup_checkpoints())
    except Exception:
        raise SystemExit(
            "체크포인트 초기화 실패. DB 연결·인증서·DDL 권한을 확인하세요."
        ) from None
    print("LangGraph checkpoint schema initialized.")
