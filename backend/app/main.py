from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.api.auth import router as auth_router
from backend.app.api.conversations import router as conversations_router
from backend.app.api.rag import router
from backend.app.api.users import router as users_router
from backend.app.core.checkpoints import open_checkpointer
from backend.app.core.database import dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with open_checkpointer() as checkpointer:
            app.state.chat_checkpointer = checkpointer
            yield
    finally:
        await dispose_engine()


app = FastAPI(title="JurisFlow", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(users_router)
app.include_router(auth_router)

app.include_router(conversations_router)
