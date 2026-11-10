from __future__ import annotations


def sample_payload(device_id: str = "GW-TEST") -> dict:
    return {
        "device_id": device_id,
        "uptime_s": 7200,
        "cpu_pct": 24.5,
        "memory_pct": 41.2,
        "wan_status": "online",
        "wan_ip": "192.0.2.44",
        "latency_ms": 14.2,
        "packet_loss_pct": 0.1,
        "dns_lookup_ms": 9.5,
        "dhcp_lease_s": 3600,
        "wifi_clients": 9,
        "wifi_rssi_avg": -52.0,
        "tx_bytes": 12000,
        "rx_bytes": 88000,
        "api_latency_ms": 175.0,
        "http_status": 200,
    }


def test_health(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "gateway-telemetry"


def test_ingest_telemetry_then_read_device(client) -> None:
    response = client.post("/api/v1/telemetry", json=sample_payload())

    assert response.status_code == 201
    assert response.json()["device_id"] == "GW-TEST"

    device = client.get("/api/v1/devices/GW-TEST", params={"window_minutes": 60})
    assert device.status_code == 200
    body = device.json()
    assert body["cpu_pct"] == 24.5
    assert body["status"] == "healthy"
    assert body["error_count"] == 0


def test_invalid_telemetry_schema_is_rejected(client) -> None:
    payload = sample_payload()
    payload["cpu_pct"] = 140

    response = client.post("/api/v1/telemetry", json=payload)

    assert response.status_code == 422


def test_unknown_device_returns_404(client) -> None:
    assert client.get("/api/v1/devices/NOPE").status_code == 404
    assert client.get("/api/v1/devices/NOPE/telemetry").status_code == 404


def test_ingest_log_extracts_signature(client) -> None:
    line = "2026-11-09T12:00:00Z [ERROR] dns: DNS resolution failed for api.vendor.net (SERVFAIL)"

    response = client.post(
        "/api/v1/logs", json={"device_id": "GW-LOG", "line": line}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["signature"] == "DNS_RESOLVE_FAILED"
    assert body["category"] == "dns"
    assert body["severity"] == "ERROR"


def test_malformed_log_returns_422(client) -> None:
    response = client.post(
        "/api/v1/logs",
        json={"device_id": "GW-LOG", "line": "this is not a syslog line"},
    )

    assert response.status_code == 422


def test_extra_payload_fields_are_rejected(client) -> None:
    payload = sample_payload()
    payload["unexpected_field"] = "nope"

    response = client.post("/api/v1/telemetry", json=payload)

    assert response.status_code == 422


def test_dashboard_and_static_assets(client) -> None:
    index = client.get("/")
    assert index.status_code == 200
    assert "Gateway Telemetry" in index.text

    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_seeded_overview(seeded_client) -> None:
    response = seeded_client.get(
        "/api/v1/metrics/overview", params={"window_minutes": 1440}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_devices"] == 6
    assert body["sample_count"] > 0
    assert 0.0 <= body["network_health_score"] <= 100.0


def test_seeded_devices_and_timeseries(seeded_client) -> None:
    devices = seeded_client.get(
        "/api/v1/devices", params={"window_minutes": 1440}
    ).json()
    assert len(devices) == 6
    assert {device["status"] for device in devices} <= {
        "healthy",
        "warning",
        "critical",
        "stale",
        "unknown",
    }

    series = seeded_client.get(
        "/api/v1/metrics/timeseries", params={"window_minutes": 1440}
    ).json()
    assert series
    assert {"bucket", "avg_latency_ms", "avg_api_latency_ms"} <= set(series[0])


def test_diagnostics_run_and_list(seeded_client) -> None:
    run = seeded_client.post(
        "/api/v1/diagnostics/run", params={"window_minutes": 1440}
    )
    assert run.status_code == 200

    results = run.json()
    assert len(results) == 6
    incidents = [item for item in results if item["category"] != "healthy"]
    assert incidents, "seeded fault profiles should produce incidents"

    incident = incidents[0]
    assert incident["root_cause"] != "No fault detected"
    assert 0.0 <= incident["confidence"] <= 0.99
    assert incident["evidence"]

    stored = seeded_client.get(
        "/api/v1/diagnostics", params={"window_minutes": 1440, "limit": 10}
    )
    assert stored.status_code == 200
    assert stored.json()


def test_root_cause_distribution(seeded_client) -> None:
    response = seeded_client.get(
        "/api/v1/metrics/root-causes", params={"window_minutes": 1440}
    )

    assert response.status_code == 200
    rows = response.json()
    assert rows
    assert {"root_cause", "category", "incidents", "avg_confidence"} <= set(rows[0])


def test_rejects_out_of_range_window(client) -> None:
    assert (
        client.get("/api/v1/metrics/overview", params={"window_minutes": 0}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/metrics/overview", params={"window_minutes": 99_999}
        ).status_code
        == 422
    )
