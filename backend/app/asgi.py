"""ASGI entry point: ``uvicorn app.asgi:app --reload`` (run from backend/)."""

from app.main import create_app

app = create_app()
