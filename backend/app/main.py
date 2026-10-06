from fastapi import FastAPI

from backend.app.api.rag import router

app = FastAPI(title="JurisFlow", version="0.1.0")
app.include_router(router)
