"""Shared pytest fixtures: temp databases and API clients."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gateway_telemetry.api import create_app  # noqa: E402
from gateway_telemetry.repository import init_db  # noqa: E402
from gateway_telemetry.services import seed_demo_data  # noqa: E402


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    app = create_app(db_path, auto_seed=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def seeded_db(db_path: Path) -> Path:
    conn = init_db(db_path)
    seed_demo_data(
        conn,
        device_count=6,
        hours=2,
        interval_s=60,
        seed=123,
        reset=True,
    )
    conn.close()
    return db_path


@pytest.fixture()
def seeded_client(seeded_db: Path) -> TestClient:
    app = create_app(seeded_db, auto_seed=False)
    with TestClient(app) as test_client:
        yield test_client
