from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.db import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"message": "Welcome to the Sales Agent API!"}
