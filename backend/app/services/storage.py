"""Managed local file storage. User-supplied names are never used as paths."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from app.core.config import Settings
from app.core.errors import NotFoundError

_EXT = {"csv": ".csv", "excel": ".xlsx"}


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def save_version_file(
    settings: Settings, dataset_id: str, version_number: int, file_format: str, content: bytes
) -> str:
    """Write bytes under ``<storage>/<dataset_id>/v<n>_<hash8><ext>``; return the relative path."""
    digest = content_hash(content)[:8]
    relative = Path(dataset_id) / f"v{version_number}_{digest}{_EXT[file_format]}"
    target = resolve_path(settings, relative.as_posix())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return relative.as_posix()


def resolve_path(settings: Settings, relative: str) -> Path:
    """Resolve a stored relative path, refusing anything outside the storage root."""
    root = settings.storage_dir.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise NotFoundError("Stored file not found.", code="FILE_NOT_FOUND")
    return target


def read_version_file(settings: Settings, relative: str) -> bytes:
    path = resolve_path(settings, relative)
    if not path.is_file():
        raise NotFoundError(
            "The stored file for this version is missing. Re-upload it or reset the demo.",
            code="FILE_NOT_FOUND",
        )
    return path.read_bytes()


def delete_file(settings: Settings, relative: str) -> None:
    path = resolve_path(settings, relative)
    path.unlink(missing_ok=True)


def clear_storage(settings: Settings) -> None:
    """Remove the whole storage directory (used by the demo reset only)."""
    root = settings.storage_dir.resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
