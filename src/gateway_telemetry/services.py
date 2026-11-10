"""Application services: ingestion, diagnostics orchestration and seeding."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from . import repository
from .diagnostics import Diagnosis, diagnose_device
from .log_parser import ParsedLog, parse_line
from .models import LogLineIn, TelemetryIn
from .simulator import build_devices, generate_batch
from .timeutil import to_iso, utcnow


def ingest_telemetry(conn: sqlite3.Connection, payload: TelemetryIn) -> tuple[int, str]:
    """Persist a validated telemetry payload, auto-registering its device."""
    values = payload.model_dump()
    ts = to_iso(payload.ts or utcnow())
    values["ts"] = ts

    repository.upsert_device(
        conn,
        payload.device_id,
        hardware_model=payload.hardware_model or "HG-5000",
        firmware=payload.firmware or "unknown",
        region=payload.region or "us-east",
    )
    telemetry_id = repository.insert_telemetry(conn, values)
    conn.commit()
    return telemetry_id, ts


def ingest_log(conn: sqlite3.Connection, payload: LogLineIn) -> tuple[int, ParsedLog]:
    """Parse and persist one raw log line."""
    parsed = parse_line(payload.line)

    repository.upsert_device(conn, payload.device_id)
    log_id = repository.insert_log(conn, payload.device_id, parsed)
    conn.commit()
    return log_id, parsed


def run_diagnostics(
    conn: sqlite3.Connection,
    window_minutes: int = 120,
    device_id: str | None = None,
) -> list[Diagnosis]:
    """Classify root causes from stored logs plus telemetry for each device."""
    end = utcnow()
    start = end - timedelta(minutes=window_minutes)
    start_iso, end_iso = to_iso(start), to_iso(end)

    if device_id is not None:
        device_ids = [device_id]
    else:
        device_ids = [
            row["device_id"]
            for row in conn.execute("SELECT device_id FROM devices ORDER BY device_id")
        ]

    results: list[Diagnosis] = []
    for current in device_ids:
        rows = repository.recent_logs(conn, current, start_iso)
        parsed_logs = [parse_line(row["raw"]) for row in rows]
        snapshot = repository.telemetry_snapshot(conn, current, start_iso)

        diagnosis = diagnose_device(
            current, parsed_logs, snapshot, start_iso, end_iso
        )
        repository.clear_diagnoses(conn, current, start_iso)
        if diagnosis.category != "healthy":
            repository.insert_diagnosis(conn, diagnosis)
        results.append(diagnosis)

    conn.commit()
    return results


def seed_demo_data(
    conn: sqlite3.Connection,
    device_count: int = 8,
    hours: int = 6,
    interval_s: int = 60,
    seed: int = 42,
    reset: bool = False,
    diagnose: bool = True,
) -> dict[str, int]:
    """Populate the database with a deterministic demo fleet."""
    if reset:
        repository.reset_database(conn)

    devices = build_devices(device_count)
    end = utcnow()
    start = end - timedelta(hours=hours)

    for device in devices:
        repository.upsert_device(
            conn,
            device.device_id,
            hardware_model=device.hardware_model,
            firmware=device.firmware,
            region=device.region,
        )

    telemetry, logs = generate_batch(devices, start, end, interval_s, seed)
    for sample in telemetry:
        repository.insert_telemetry(conn, sample.as_dict())
    for log in logs:
        repository.insert_log(conn, log.device_id, parse_line(log.raw))
    conn.commit()

    diagnoses = (
        run_diagnostics(conn, window_minutes=min(hours * 60, 1440)) if diagnose else []
    )

    return {
        "devices": len(devices),
        "telemetry": len(telemetry),
        "logs": len(logs),
        "diagnoses": sum(1 for item in diagnoses if item.category != "healthy"),
    }


def seed_if_empty(conn: sqlite3.Connection) -> bool:
    """Seed demo data only when no telemetry exists yet."""
    count = conn.execute("SELECT COUNT(*) AS c FROM telemetry").fetchone()["c"]
    if count:
        return False
    seed_demo_data(conn)
    return True
