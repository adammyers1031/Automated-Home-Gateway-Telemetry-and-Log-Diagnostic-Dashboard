from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from gateway_telemetry import repository
from gateway_telemetry.timeutil import to_iso, utcnow


def _row(device_id: str, ts: str, **overrides: object) -> dict:
    row = {
        "device_id": device_id,
        "ts": ts,
        "uptime_s": 3600,
        "cpu_pct": 30.0,
        "memory_pct": 45.0,
        "wan_status": "online",
        "wan_ip": "192.0.2.10",
        "latency_ms": 18.0,
        "packet_loss_pct": 0.2,
        "dns_lookup_ms": 12.0,
        "dhcp_lease_s": 7200,
        "wifi_clients": 10,
        "wifi_rssi_avg": -55.0,
        "tx_bytes": 1000,
        "rx_bytes": 5000,
        "api_latency_ms": 180.0,
        "http_status": 200,
    }
    row.update(overrides)
    return row


@pytest.fixture()
def conn(tmp_path: Path):
    connection = repository.init_db(tmp_path / "repo.db")
    yield connection
    connection.close()


def test_overview_aggregates_window(conn) -> None:
    now = utcnow()
    repository.upsert_device(conn, "GW-001")
    repository.upsert_device(conn, "GW-002")
    for offset in (5, 3, 1):
        repository.insert_telemetry(conn, _row("GW-001", to_iso(now - timedelta(minutes=offset))))
    repository.insert_telemetry(
        conn, _row("GW-002", to_iso(now - timedelta(minutes=2)), http_status=503, latency_ms=40)
    )
    conn.commit()

    overview = repository.overview_metrics(conn, to_iso(now - timedelta(minutes=30)))

    assert overview["total_devices"] == 2
    assert overview["online_devices"] == 2
    assert overview["sample_count"] == 4
    assert overview["avg_packet_loss_pct"] == pytest.approx(0.2, abs=1e-3)
    assert overview["api_error_rate_pct"] == pytest.approx(25.0)
    assert 0.0 <= overview["network_health_score"] <= 100.0


def test_overview_is_zero_when_window_empty(conn) -> None:
    overview = repository.overview_metrics(conn, to_iso(utcnow()))

    assert overview["sample_count"] == 0
    assert overview["network_health_score"] == 0.0


def test_timeseries_buckets_by_minute(conn) -> None:
    now = utcnow().replace(second=0, microsecond=0)
    repository.upsert_device(conn, "GW-001")
    for offset in (5, 5, 1):
        repository.insert_telemetry(conn, _row("GW-001", to_iso(now - timedelta(minutes=offset))))
    conn.commit()

    series = repository.timeseries(conn, to_iso(now - timedelta(minutes=30)))

    assert len(series) == 2  # two distinct minute buckets
    assert all("avg_latency_ms" in bucket for bucket in series)


def test_latest_devices_reports_status(conn) -> None:
    now = utcnow()
    repository.upsert_device(conn, "GW-001")
    repository.insert_telemetry(conn, _row("GW-001", to_iso(now - timedelta(minutes=1))))
    conn.commit()

    devices = repository.latest_devices(conn, to_iso(now - timedelta(minutes=30)))

    assert len(devices) == 1
    assert devices[0]["device_id"] == "GW-001"
    assert devices[0]["status"] == "healthy"


def test_telemetry_snapshot_computes_ratios(conn) -> None:
    now = utcnow()
    repository.upsert_device(conn, "GW-001")
    repository.insert_telemetry(conn, _row("GW-001", to_iso(now - timedelta(minutes=2))))
    repository.insert_telemetry(
        conn,
        _row(
            "GW-001",
            to_iso(now - timedelta(minutes=1)),
            wan_status="offline",
            http_status=500,
            packet_loss_pct=20.0,
        ),
    )
    conn.commit()

    snapshot = repository.telemetry_snapshot(
        conn, "GW-001", to_iso(now - timedelta(minutes=30))
    )

    assert snapshot.samples == 2
    assert snapshot.wan_online_ratio == pytest.approx(0.5)
    assert snapshot.api_error_ratio == pytest.approx(0.5)
    assert snapshot.avg_packet_loss_pct == pytest.approx(10.1)


def test_devices_without_telemetry_are_unknown(conn) -> None:
    repository.upsert_device(conn, "GW-EMPTY")
    conn.commit()

    devices = repository.latest_devices(conn, to_iso(utcnow()))

    assert devices[0]["status"] == "unknown"
    assert devices[0]["last_seen"] is None
