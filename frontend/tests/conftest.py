"""Spin up a real backend (temp DB, demo data) on a free port and point the frontend at it."""

from __future__ import annotations

import os
import pathlib
import socket
import sys
import threading
import time

import pytest
import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
# frontend/app.py and the backend package are both called "app". They normally live in separate
# processes; here we import the backend package first so it wins in sys.modules.
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings  # noqa: E402
from app.demo.seed import reset_demo  # noqa: E402
from app.main import create_app  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(settings):
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            requests.get(f"http://127.0.0.1:{port}/api/health", timeout=0.5)
            break
        except requests.RequestException:
            time.sleep(0.1)
    return server, f"http://127.0.0.1:{port}/api"


@pytest.fixture(scope="session")
def demo_api(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("demo")
    settings = Settings(database_url=f"sqlite:///{(tmp / 'd.db').as_posix()}", storage_dir=tmp / "storage")
    reset_demo(settings, with_v2=True, sample_dir=tmp / "sample")
    server, url = _serve(settings)
    os.environ["DS_API_URL"] = url
    yield url
    server.should_exit = True


@pytest.fixture
def empty_api(tmp_path):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'e.db').as_posix()}", storage_dir=tmp_path / "storage")
    server, url = _serve(settings)
    previous = os.environ.get("DS_API_URL")
    os.environ["DS_API_URL"] = url
    yield url
    server.should_exit = True
    if previous:
        os.environ["DS_API_URL"] = previous
