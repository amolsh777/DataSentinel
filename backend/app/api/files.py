from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.deps import get_settings_dep, read_upload
from app.core.config import Settings
from app.schemas.common import ErrorResponse
from app.schemas.dataset import FileInspectOut
from app.services import dataset_service

router = APIRouter(prefix="/files", tags=["files"])


@router.post(
    "/inspect",
    response_model=FileInspectOut,
    responses={413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def inspect_upload(
    file: UploadFile = File(...),
    sheet_name: str | None = Form(None),
    settings: Settings = Depends(get_settings_dep),
) -> FileInspectOut:
    """Validate and preview a file without registering it (use for Excel sheet selection)."""
    content = await read_upload(file, settings.max_upload_bytes)
    return FileInspectOut(
        **dataset_service.inspect_file(settings, content, file.filename or "", sheet_name)
    )
