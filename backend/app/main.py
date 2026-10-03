from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.api.rag import router
from backend.app.core.database import dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await dispose_engine()


app = FastAPI(title="JurisFlow", version="0.1.0", lifespan=lifespan)
app.include_router(router)
