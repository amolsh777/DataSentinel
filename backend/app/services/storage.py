"""Managed local file storage: CSV/Excel save/read, Parquet cache, safety checks."""

from __future__ import annotations

import hashlib
import io
import shutil
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.core.config import Settings
from app.core.errors import AppError, NotFoundError

_EXT = {"csv": ".csv", "excel": ".xlsx"}
# Parquet sits next to the original but in a sibling directory
_PARQUET_SUBDIR = "parquet_cache"

# Safety limits for Excel (zip-bomb / XXE)
_MAX_XLSX_UNCOMPRESSED_MB = 500
_MAX_XLSX_ENTRY_COUNT = 1000


class FileSafetyError(AppError):
    status_code = 422
    code = "FILE_SAFETY_ERROR"


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# ─────────────────────────── save / read raw files ───────────────────────────

def save_version_file(
    settings: Settings, dataset_id: str, version_number: int, file_format: str, content: bytes
) -> str:
    """Write bytes; return relative path. Runs xlsx safety check first."""
    if file_format == "excel":
        _check_xlsx_safety(content)
    digest = content_hash(content)[:8]
    relative = Path(dataset_id) / f"v{version_number}_{digest}{_EXT[file_format]}"
    target = resolve_path(settings, relative.as_posix())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return relative.as_posix()


def _check_xlsx_safety(content: bytes) -> None:
    """Reject zip bombs and suspiciously large workbooks before parsing."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            entries = zf.infolist()
            if len(entries) > _MAX_XLSX_ENTRY_COUNT:
                raise FileSafetyError(
                    f"XLSX contains {len(entries)} entries (limit {_MAX_XLSX_ENTRY_COUNT}). "
                    "This may be a malformed or malicious file."
                )
            total_uncompressed = sum(e.file_size for e in entries)
            limit_bytes = _MAX_XLSX_UNCOMPRESSED_MB * 1024 * 1024
            if total_uncompressed > limit_bytes:
                raise FileSafetyError(
                    f"XLSX uncompressed content is {total_uncompressed // (1024*1024)} MB "
                    f"(limit {_MAX_XLSX_UNCOMPRESSED_MB} MB). Possible zip bomb."
                )
    except zipfile.BadZipFile as exc:
        raise FileSafetyError(f"File is not a valid XLSX/zip: {exc}") from None


def resolve_path(settings: Settings, relative: str) -> Path:
    root = settings.storage_dir.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise NotFoundError("Stored file not found.", code="FILE_NOT_FOUND")
    return target


def read_version_file(settings: Settings, relative: str) -> bytes:
    path = resolve_path(settings, relative)
    if not path.is_file():
        raise NotFoundError(
            "The stored file for this version is missing. "
            "Re-upload it, or run `python scripts/reset_demo.py --yes` to restore demo data.",
            code="FILE_NOT_FOUND",
        )
    return path.read_bytes()


def delete_file(settings: Settings, relative: str) -> None:
    path = resolve_path(settings, relative)
    path.unlink(missing_ok=True)


def clear_storage(settings: Settings) -> None:
    root = settings.storage_dir.resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


# ─────────────────────────── Parquet cache ──────────────────────────────────


def _parquet_path(settings: Settings, relative_csv: str) -> Path:
    """Return the expected Parquet cache path for a stored version."""
    rel = Path(relative_csv)
    cache_dir = settings.storage_dir.resolve() / _PARQUET_SUBDIR / rel.parent
    return cache_dir / (rel.stem + ".parquet")


def save_parquet_cache(settings: Settings, df: pd.DataFrame, relative_csv: str) -> str:
    """Write a Parquet file alongside the original; return its relative path."""
    dest = _parquet_path(settings, relative_csv)
    dest.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, dest, compression="snappy")
    # Relative path from storage_dir root
    rel = dest.relative_to(settings.storage_dir.resolve())
    return rel.as_posix()


def load_parquet_cache(settings: Settings, parquet_relative: str) -> pd.DataFrame | None:
    """Return the cached DataFrame or None if the file is missing."""
    path = settings.storage_dir.resolve() / parquet_relative
    if not path.is_file():
        return None
    return pq.read_table(path).to_pandas()


def load_version_dataframe_cached(settings: Settings, version) -> pd.DataFrame:
    """Load from Parquet cache when available, else parse the raw file and cache it.

    ``version`` is a DatasetVersion ORM instance.
    Falls back gracefully if Parquet write fails (e.g. storage full).
    """
    # Try Parquet cache first.
    if version.parquet_path:
        df = load_parquet_cache(settings, version.parquet_path)
        if df is not None:
            return df

    # Parse from original file.
    from app.services.ingestion import parse_file
    content = read_version_file(settings, version.storage_path)
    filename = "stored.csv" if version.file_format == "csv" else "stored.xlsx"
    result = parse_file(
        content, filename, sheet_name=version.sheet_name,
        max_rows=max(settings.max_rows, version.row_count),
        max_columns=max(settings.max_columns, version.column_count),
    )
    df = result.df

    # Write cache (best-effort; never raises).
    try:
        parquet_rel = save_parquet_cache(settings, df, version.storage_path)
        # Update the DB record in a fresh session so we don't dirty the caller's session.
        _update_parquet_path(settings, version.id, parquet_rel)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Parquet cache write failed: %s", exc)

    return df


def _update_parquet_path(settings: Settings, version_id: str, parquet_rel: str) -> None:
    from app.database.session import build_engine, build_session_factory
    from app.models import DatasetVersion as DV
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    with factory() as db:
        v = db.get(DV, version_id)
        if v:
            v.parquet_path = parquet_rel
            db.commit()


def invalidate_parquet_cache(settings: Settings, version) -> None:
    """Remove a cached Parquet file when the version is updated (type overrides etc.)."""
    if version.parquet_path:
        path = settings.storage_dir.resolve() / version.parquet_path
        path.unlink(missing_ok=True)


__all__ = [
    "FileSafetyError", "content_hash",
    "save_version_file", "resolve_path", "read_version_file",
    "delete_file", "clear_storage",
    "save_parquet_cache", "load_parquet_cache",
    "load_version_dataframe_cached", "invalidate_parquet_cache",
]
