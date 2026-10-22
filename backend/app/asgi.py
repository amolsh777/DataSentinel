"""ASGI entry point: ``uvicorn app.asgi:app --reload`` (run from backend/)."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.main import create_app
from app.services import scheduler_service

@asynccontextmanager
async def lifespan(appl: FastAPI):
    scheduler_service.start_scheduler()
    try:
        yield
    finally:
        scheduler_service.stop_scheduler()

app = create_app(lifespan=lifespan)
