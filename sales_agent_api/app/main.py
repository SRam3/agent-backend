from fastapi import FastAPI

from .db import init_db

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    await init_db()

@app.get("/")
async def root():
    return {"message": "Welcome to the Sales Agent API!"}


@app.get("/health")
async def health_check():
    """Endpoint used by the LLM to verify connectivity with the backend."""
    return {"status": "ok", "message": "Backend reachable by LLM"}

