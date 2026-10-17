"""FastAPI dependencies: DB session, settings, bounded upload reading."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request, UploadFile
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import IngestionError, PayloadTooLargeError

_CHUNK = 1024 * 1024


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    db: Session = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


async def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload in chunks, aborting as soon as the size limit is exceeded."""
    if not file.filename:
        raise IngestionError("A file name is required.", code="MISSING_FILENAME")
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise PayloadTooLargeError(
                f"File exceeds the {max_bytes / (1024 * 1024):.1f} MB upload limit.",
                details={"limit_bytes": max_bytes},
            )
        chunks.append(chunk)
    return b"".join(chunks)
