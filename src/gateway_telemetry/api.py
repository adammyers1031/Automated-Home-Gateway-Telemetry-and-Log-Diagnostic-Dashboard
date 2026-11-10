"""FastAPI application: telemetry/log ingestion, SQL metrics and dashboard."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, repository
from .config import (
    AUTO_SEED,
    DASHBOARD_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_WINDOW_MINUTES,
    MAX_WINDOW_MINUTES,
)
from .log_parser import LogParseError
from .models import (
    DiagnosisOut,
    LogAccepted,
    LogLineIn,
    TelemetryAccepted,
    TelemetryIn,
)
from .services import ingest_log, ingest_telemetry, run_diagnostics, seed_if_empty
from .timeutil import iso_minutes_ago


def get_conn(request: Request):
    """Yield one SQLite connection per request."""
    connection = repository.connect(request.app.state.db_path)
    try:
        yield connection
    finally:
        connection.close()


ConnectionDep = Annotated[sqlite3.Connection, Depends(get_conn)]
WindowMinutes = Annotated[int, Query(ge=1, le=MAX_WINDOW_MINUTES)]


def create_app(
    db_path: str | Path | None = None,
    auto_seed: bool | None = None,
) -> FastAPI:
    """Build the app. Tests pass a temp database and ``auto_seed=False``."""
    resolved_db = Path(db_path or DEFAULT_DB_PATH)
    should_seed = AUTO_SEED if auto_seed is None else auto_seed

    conn = repository.init_db(resolved_db)
    if should_seed:
        seed_if_empty(conn)
    conn.close()

    app = FastAPI(
        title="Home Gateway Telemetry & Diagnostics API",
        version=__version__,
        description=(
            "Ingests simulated home-gateway telemetry and syslog lines, parses "
            "failure signatures, classifies root causes and serves SQL-backed "
            "network health metrics to the monitoring dashboard."
        ),
    )
    app.state.db_path = resolved_db

    if DASHBOARD_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(DASHBOARD_DIR / "index.html")

    @app.get("/health", tags=["system"], summary="Liveness probe")
    def health() -> dict:
        return {
            "status": "ok",
            "service": "gateway-telemetry",
            "version": __version__,
        }

    @app.post(
        "/api/v1/telemetry",
        response_model=TelemetryAccepted,
        status_code=201,
        tags=["ingestion"],
        summary="Ingest one telemetry heartbeat",
    )
    def post_telemetry(
        payload: TelemetryIn, conn: ConnectionDep
    ) -> TelemetryAccepted:
        telemetry_id, ts = ingest_telemetry(conn, payload)
        return TelemetryAccepted(id=telemetry_id, device_id=payload.device_id, ts=ts)

    @app.post(
        "/api/v1/logs",
        response_model=LogAccepted,
        status_code=201,
        tags=["ingestion"],
        summary="Ingest and parse one syslog line",
    )
    def post_log(payload: LogLineIn, conn: ConnectionDep) -> LogAccepted:
        try:
            log_id, parsed = ingest_log(conn, payload)
        except LogParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return LogAccepted(
            id=log_id,
            device_id=payload.device_id,
            ts=parsed.ts,
            severity=parsed.severity,
            component=parsed.component,
            message=parsed.message,
            signature=parsed.code,
            category=parsed.category,
        )

    @app.get(
        "/api/v1/devices",
        tags=["devices"],
        summary="Fleet state with the latest sample per device",
    )
    def get_devices(
        conn: ConnectionDep,
        window_minutes: WindowMinutes = DEFAULT_WINDOW_MINUTES,
    ) -> list[dict]:
        return repository.latest_devices(conn, iso_minutes_ago(window_minutes))

    @app.get(
        "/api/v1/devices/{device_id}",
        tags=["devices"],
        summary="One device with its latest telemetry",
    )
    def get_device(
        device_id: str,
        conn: ConnectionDep,
        window_minutes: WindowMinutes = DEFAULT_WINDOW_MINUTES,
    ) -> dict:
        device = repository.get_device(conn, device_id, iso_minutes_ago(window_minutes))
        if device is None:
            raise HTTPException(status_code=404, detail=f"Unknown device: {device_id}")
        return device

    @app.get(
        "/api/v1/devices/{device_id}/telemetry",
        tags=["devices"],
        summary="Raw telemetry samples for one device",
    )
    def get_device_telemetry(
        device_id: str,
        conn: ConnectionDep,
        window_minutes: WindowMinutes = DEFAULT_WINDOW_MINUTES,
        limit: Annotated[int, Query(ge=1, le=10_000)] = 2000,
    ) -> list[dict]:
        since = iso_minutes_ago(window_minutes)
        if repository.get_device(conn, device_id, since) is None:
            raise HTTPException(status_code=404, detail=f"Unknown device: {device_id}")
        return repository.telemetry_series(conn, device_id, since, limit=limit)

    @app.get(
        "/api/v1/devices/{device_id}/logs",
        tags=["devices"],
        summary="Recent parsed log lines for one device",
    )
    def get_device_logs(
        device_id: str,
        conn: ConnectionDep,
        window_minutes: WindowMinutes = DEFAULT_WINDOW_MINUTES,
        limit: Annotated[int, Query(ge=1, le=2000)] = 200,
    ) -> list[dict]:
        return repository.recent_logs(
            conn, device_id, iso_minutes_ago(window_minutes), limit=limit
        )

    @app.get(
        "/api/v1/metrics/overview",
        tags=["metrics"],
        summary="Fleet KPIs and composite network health score",
    )
    def get_overview(
        conn: ConnectionDep,
        window_minutes: WindowMinutes = DEFAULT_WINDOW_MINUTES,
    ) -> dict:
        return repository.overview_metrics(conn, iso_minutes_ago(window_minutes))

    @app.get(
        "/api/v1/metrics/timeseries",
        tags=["metrics"],
        summary="One-minute latency, loss and API error buckets",
    )
    def get_timeseries(
        conn: ConnectionDep,
        window_minutes: WindowMinutes = DEFAULT_WINDOW_MINUTES,
        device_id: Annotated[str | None, Query()] = None,
    ) -> list[dict]:
        return repository.timeseries(
            conn, iso_minutes_ago(window_minutes), device_id=device_id
        )

    @app.get(
        "/api/v1/metrics/root-causes",
        tags=["metrics"],
        summary="Incident counts grouped by diagnosed root cause",
    )
    def get_root_causes(
        conn: ConnectionDep,
        window_minutes: WindowMinutes = 1440,
    ) -> list[dict]:
        return repository.root_cause_distribution(conn, iso_minutes_ago(window_minutes))

    @app.post(
        "/api/v1/diagnostics/run",
        response_model=list[DiagnosisOut],
        tags=["diagnostics"],
        summary="Run the root-cause classifier and persist incidents",
    )
    def post_run_diagnostics(
        conn: ConnectionDep,
        window_minutes: WindowMinutes = DEFAULT_WINDOW_MINUTES,
        device_id: Annotated[str | None, Query()] = None,
    ) -> list[DiagnosisOut]:
        results = run_diagnostics(
            conn, window_minutes=window_minutes, device_id=device_id
        )
        return [DiagnosisOut(**asdict(result)) for result in results]

    @app.get(
        "/api/v1/diagnostics",
        tags=["diagnostics"],
        summary="Stored incidents, newest first",
    )
    def get_diagnostics(
        conn: ConnectionDep,
        window_minutes: WindowMinutes = 1440,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> list[dict]:
        return repository.list_diagnoses(
            conn, iso_minutes_ago(window_minutes), limit=limit
        )

    return app
