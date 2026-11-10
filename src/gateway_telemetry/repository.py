"""SQLite persistence and SQL aggregation queries.

All reads used by the dashboard live here so the aggregation logic is
reviewable in one place. Every query is parameterised; no user input is
interpolated into SQL.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from .config import SQL_DIR
from .diagnostics import Diagnosis, TelemetrySnapshot
from .log_parser import ParsedLog
from .timeutil import to_iso, utcnow

TELEMETRY_FIELDS = (
    "device_id",
    "ts",
    "uptime_s",
    "cpu_pct",
    "memory_pct",
    "wan_status",
    "wan_ip",
    "latency_ms",
    "packet_loss_pct",
    "dns_lookup_ms",
    "dhcp_lease_s",
    "wifi_clients",
    "wifi_rssi_avg",
    "tx_bytes",
    "rx_bytes",
    "api_latency_ms",
    "http_status",
)


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with a row factory and sane pragmas."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI may create the dependency and run the endpoint in different
    # worker threads. Each request owns its own connection, so cross-thread use
    # is safe as long as a connection is never shared between concurrent calls.
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Create the schema if it does not exist and return a connection."""
    conn = connect(db_path)
    schema = (SQL_DIR / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def upsert_device(
    conn: sqlite3.Connection,
    device_id: str,
    hardware_model: str = "HG-5000",
    firmware: str = "2.4.1",
    region: str = "us-east",
) -> None:
    conn.execute(
        """
        INSERT INTO devices (device_id, hardware_model, firmware, region, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET
            hardware_model = COALESCE(excluded.hardware_model, devices.hardware_model),
            firmware       = COALESCE(excluded.firmware, devices.firmware),
            region         = COALESCE(excluded.region, devices.region)
        """,
        (device_id, hardware_model, firmware, region, to_iso(utcnow())),
    )


def insert_telemetry(conn: sqlite3.Connection, values: Mapping[str, object]) -> int:
    row = {field: values.get(field) for field in TELEMETRY_FIELDS}
    columns = ", ".join(TELEMETRY_FIELDS)
    placeholders = ", ".join("?" for _ in TELEMETRY_FIELDS)
    cursor = conn.execute(
        f"INSERT INTO telemetry ({columns}) VALUES ({placeholders})",
        [row[field] for field in TELEMETRY_FIELDS],
    )
    return int(cursor.lastrowid or 0)


def insert_log(conn: sqlite3.Connection, device_id: str, parsed: ParsedLog) -> int:
    cursor = conn.execute(
        """
        INSERT INTO device_logs
            (device_id, ts, severity, component, message, signature, category, raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            device_id,
            parsed.ts,
            parsed.severity,
            parsed.component,
            parsed.message,
            parsed.code,
            parsed.category,
            parsed.raw,
        ),
    )
    return int(cursor.lastrowid or 0)


def insert_diagnosis(conn: sqlite3.Connection, diagnosis: Diagnosis) -> int:
    cursor = conn.execute(
        """
        INSERT INTO diagnoses
            (device_id, created_at, window_start, window_end, root_cause,
             category, confidence, severity, evidence, recommended_action)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            diagnosis.device_id,
            to_iso(utcnow()),
            diagnosis.window_start,
            diagnosis.window_end,
            diagnosis.root_cause,
            diagnosis.category,
            diagnosis.confidence,
            diagnosis.severity,
            json.dumps(diagnosis.evidence),
            diagnosis.recommended_action,
        ),
    )
    return int(cursor.lastrowid or 0)


def clear_diagnoses(
    conn: sqlite3.Connection, device_id: str, since: str
) -> None:
    """Remove a device's diagnoses created inside the analysis window.

    Keeps repeated classifier runs idempotent instead of stacking duplicate
    incidents for the same fault.
    """
    conn.execute(
        "DELETE FROM diagnoses WHERE device_id = ? AND created_at >= ?",
        (device_id, since),
    )


def reset_database(conn: sqlite3.Connection) -> None:
    """Delete all rows, respecting foreign keys."""
    for table in ("diagnoses", "device_logs", "telemetry", "devices"):
        conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
    conn.commit()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _device_status(row: sqlite3.Row, since: str) -> str:
    if row["last_seen"] is None:
        return "unknown"
    if row["last_seen"] < since:
        return "stale"
    if row["wan_status"] == "offline":
        return "critical"
    if row["wan_status"] == "degraded":
        return "warning"
    if row["error_count"] and row["error_count"] >= 3:
        return "warning"
    if row["packet_loss_pct"] is not None and row["packet_loss_pct"] >= 5:
        return "warning"
    return "healthy"


_LATEST_DEVICES_SQL = """
SELECT
    d.device_id,
    d.hardware_model,
    d.firmware,
    d.region,
    t.ts               AS last_seen,
    t.wan_status,
    t.latency_ms,
    t.packet_loss_pct,
    t.cpu_pct,
    t.memory_pct,
    t.api_latency_ms,
    t.http_status,
    COALESCE(e.error_count, 0) AS error_count
FROM devices d
LEFT JOIN (
    SELECT * FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY device_id ORDER BY ts DESC) AS rn
        FROM telemetry
    ) WHERE rn = 1
) t ON t.device_id = d.device_id
LEFT JOIN (
    SELECT device_id, COUNT(*) AS error_count
    FROM device_logs
    WHERE ts >= ? AND severity IN ('ERROR', 'CRITICAL')
    GROUP BY device_id
) e ON e.device_id = d.device_id
ORDER BY d.device_id
"""


def latest_devices(conn: sqlite3.Connection, since: str) -> list[dict]:
    rows = conn.execute(_LATEST_DEVICES_SQL, (since,)).fetchall()
    devices: list[dict] = []
    for row in rows:
        item = dict(row)
        item["status"] = _device_status(row, since)
        devices.append(item)
    return devices


def get_device(conn: sqlite3.Connection, device_id: str, since: str) -> dict | None:
    for device in latest_devices(conn, since):
        if device["device_id"] == device_id:
            return device
    return None


def telemetry_series(
    conn: sqlite3.Connection, device_id: str, since: str, limit: int = 2000
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ts, uptime_s, cpu_pct, memory_pct, wan_status, latency_ms,
               packet_loss_pct, dns_lookup_ms, api_latency_ms, http_status
        FROM telemetry
        WHERE device_id = ? AND ts >= ?
        ORDER BY ts
        LIMIT ?
        """,
        (device_id, since, limit),
    ).fetchall()
    return [dict(row) for row in rows]


_OVERVIEW_SQL = """
WITH windowed AS (
    SELECT * FROM telemetry WHERE ts >= ?
),
latest AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY device_id ORDER BY ts DESC) AS rn
    FROM windowed
),
current AS (
    SELECT * FROM latest WHERE rn = 1
),
stats AS (
    SELECT
        (SELECT COUNT(*) FROM devices)                              AS total_devices,
        (SELECT COUNT(*) FROM current WHERE wan_status = 'online')  AS online_devices,
        COALESCE((SELECT AVG(latency_ms) FROM windowed), 0)         AS avg_latency_ms,
        COALESCE((SELECT AVG(packet_loss_pct) FROM windowed), 0)    AS avg_packet_loss_pct,
        COALESCE((SELECT AVG(api_latency_ms) FROM windowed), 0)     AS avg_api_latency_ms,
        COALESCE((SELECT AVG(cpu_pct) FROM windowed), 0)            AS avg_cpu_pct,
        COALESCE((SELECT AVG(memory_pct) FROM windowed), 0)         AS avg_memory_pct,
        COALESCE((
            SELECT 100.0 * SUM(CASE WHEN http_status >= 500 THEN 1 ELSE 0 END) / COUNT(*)
            FROM windowed
        ), 0)                                                       AS api_error_rate_pct,
        (SELECT COUNT(*) FROM windowed)                             AS sample_count,
        (SELECT COUNT(*) FROM diagnoses WHERE created_at >= ?)      AS incident_count
)
SELECT
    *,
    CASE WHEN sample_count = 0 THEN 0.0 ELSE ROUND(MAX(0.0, MIN(100.0,
        100.0
        - 40.0 * (1.0 - 1.0 * online_devices / NULLIF(total_devices, 0))
        - MIN(25.0, avg_packet_loss_pct * 5.0)
        - MIN(20.0, avg_latency_ms / 20.0)
        - MIN(15.0, api_error_rate_pct)
    )), 1) END AS network_health_score
FROM stats
"""


def overview_metrics(conn: sqlite3.Connection, since: str) -> dict:
    row = conn.execute(_OVERVIEW_SQL, (since, since)).fetchone()
    if row is None:
        return {}
    result = dict(row)
    for key, value in result.items():
        if isinstance(value, float):
            result[key] = round(value, 3)
    result["total_devices"] = int(result.get("total_devices") or 0)
    result["online_devices"] = int(result.get("online_devices") or 0)
    result["sample_count"] = int(result.get("sample_count") or 0)
    result["incident_count"] = int(result.get("incident_count") or 0)
    return result


def timeseries(
    conn: sqlite3.Connection, since: str, device_id: str | None = None
) -> list[dict]:
    query = """
        SELECT
            substr(ts, 1, 16) || ':00Z' AS bucket,
            ROUND(AVG(latency_ms), 2)      AS avg_latency_ms,
            ROUND(AVG(packet_loss_pct), 3) AS avg_packet_loss_pct,
            ROUND(AVG(api_latency_ms), 2)  AS avg_api_latency_ms,
            ROUND(AVG(cpu_pct), 1)         AS avg_cpu_pct,
            ROUND(AVG(memory_pct), 1)      AS avg_memory_pct,
            ROUND(
                100.0 * SUM(CASE WHEN http_status >= 500 THEN 1 ELSE 0 END) / COUNT(*),
                2
            ) AS api_error_rate_pct
        FROM telemetry
        WHERE ts >= ?
    """
    params: list[object] = [since]
    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)
    query += " GROUP BY bucket ORDER BY bucket"
    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def root_cause_distribution(conn: sqlite3.Connection, since: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            root_cause,
            category,
            COUNT(*)                  AS incidents,
            ROUND(AVG(confidence), 3) AS avg_confidence,
            MAX(created_at)           AS last_seen
        FROM diagnoses
        WHERE created_at >= ?
        GROUP BY root_cause, category
        ORDER BY incidents DESC
        """,
        (since,),
    ).fetchall()
    return [dict(row) for row in rows]


def list_diagnoses(conn: sqlite3.Connection, since: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """
        SELECT device_id, created_at, window_start, window_end, root_cause,
               category, confidence, severity, evidence, recommended_action
        FROM diagnoses
        WHERE created_at >= ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (since, limit),
    ).fetchall()

    diagnoses: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item["evidence"])
        except (TypeError, json.JSONDecodeError):
            item["evidence"] = []
        diagnoses.append(item)
    return diagnoses


def recent_logs(
    conn: sqlite3.Connection, device_id: str, since: str, limit: int = 1000
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ts, severity, component, message, raw
        FROM device_logs
        WHERE device_id = ? AND ts >= ?
        ORDER BY ts DESC
        LIMIT ?
        """,
        (device_id, since, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def telemetry_snapshot(
    conn: sqlite3.Connection, device_id: str, since: str
) -> TelemetrySnapshot:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS samples,
            COALESCE(AVG(CASE WHEN wan_status = 'online' THEN 1.0 ELSE 0.0 END), 0)
                AS wan_online_ratio,
            COALESCE(AVG(packet_loss_pct), 0) AS avg_packet_loss_pct,
            COALESCE(AVG(cpu_pct), 0) AS avg_cpu_pct,
            COALESCE(AVG(memory_pct), 0) AS avg_memory_pct,
            COALESCE(AVG(api_latency_ms), 0) AS avg_api_latency_ms,
            COALESCE(AVG(CASE WHEN http_status >= 500 THEN 1.0 ELSE 0.0 END), 0)
                AS api_error_ratio,
            COALESCE(AVG(dns_lookup_ms), 0) AS avg_dns_lookup_ms,
            COALESCE(AVG(wifi_rssi_avg), 0) AS avg_wifi_rssi,
            COALESCE(AVG(wifi_clients), 0) AS avg_wifi_clients
        FROM telemetry
        WHERE device_id = ? AND ts >= ?
        """,
        (device_id, since),
    ).fetchone()

    if row is None:
        return TelemetrySnapshot()

    return TelemetrySnapshot(
        samples=int(row["samples"] or 0),
        wan_online_ratio=float(row["wan_online_ratio"] or 0),
        avg_packet_loss_pct=float(row["avg_packet_loss_pct"] or 0),
        avg_cpu_pct=float(row["avg_cpu_pct"] or 0),
        avg_memory_pct=float(row["avg_memory_pct"] or 0),
        avg_api_latency_ms=float(row["avg_api_latency_ms"] or 0),
        api_error_ratio=float(row["api_error_ratio"] or 0),
        avg_dns_lookup_ms=float(row["avg_dns_lookup_ms"] or 0),
        avg_wifi_rssi=float(row["avg_wifi_rssi"] or 0),
        avg_wifi_clients=float(row["avg_wifi_clients"] or 0),
    )
