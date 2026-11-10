"""Paths and runtime settings."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
SQL_DIR = PROJECT_ROOT / "sql"
DATA_DIR = PROJECT_ROOT / "data"

DEFAULT_DB_PATH = Path(
    os.environ.get("GATEWAY_DB_PATH", str(DATA_DIR / "telemetry.db"))
)
DEFAULT_WINDOW_MINUTES = int(os.environ.get("GATEWAY_WINDOW_MINUTES", "120"))
AUTO_SEED = os.environ.get("GATEWAY_AUTO_SEED", "1") == "1"

MAX_WINDOW_MINUTES = 10_080  # seven days
