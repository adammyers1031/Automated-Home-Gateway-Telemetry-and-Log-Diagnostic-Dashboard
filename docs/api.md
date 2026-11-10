# API reference

Base URL: `http://127.0.0.1:8000` · Interactive docs: `/docs` · Schema: `/openapi.json`

All timestamps are UTC ISO-8601 (`YYYY-MM-DDTHH:MM:SSZ`). Query windows are in
minutes and capped at 10,080 (7 days).

## System

### `GET /health`

```json
{ "status": "ok", "service": "gateway-telemetry", "version": "0.1.0" }
```

## Ingestion

### `POST /api/v1/telemetry` → `201`

Required fields:

| Field | Type | Constraint |
|---|---|---|
| `device_id` | string | 3–64 chars, `[A-Za-z0-9._-]` |
| `uptime_s` | int | ≥ 0 |
| `cpu_pct` | float | 0–100 |
| `memory_pct` | float | 0–100 |
| `wan_status` | enum | `online` / `offline` / `degraded` |
| `packet_loss_pct` | float | 0–100 |
| `wifi_clients` | int | ≥ 0 |
| `tx_bytes`, `rx_bytes` | int | ≥ 0 |
| `http_status` | int | 100–599 |

Optional: `ts`, `wan_ip`, `latency_ms`, `dns_lookup_ms`, `dhcp_lease_s`,
`wifi_rssi_avg`, `api_latency_ms`, `hardware_model`, `firmware`, `region`.
Unknown fields are rejected (`extra="forbid"`).

Unknown devices are auto-registered on first ingest.

```json
{ "id": 1, "device_id": "GW-900", "ts": "2026-11-09T12:00:00Z", "status": "accepted" }
```

Invalid payload → `422` with a Pydantic error list.

### `POST /api/v1/logs` → `201`

```json
{
  "device_id": "GW-900",
  "line": "2026-11-09T12:00:00Z [ERROR] dns: DNS resolution failed for api.vendor.net"
}
```

Response includes extracted fields and the matched signature:

```json
{
  "id": 10,
  "device_id": "GW-900",
  "ts": "2026-11-09T12:00:00Z",
  "severity": "ERROR",
  "component": "dns",
  "message": "DNS resolution failed for api.vendor.net",
  "signature": "DNS_RESOLVE_FAILED",
  "category": "dns"
}
```

Malformed line → `422`.

## Devices

| Endpoint | Notes |
|---|---|
| `GET /api/v1/devices?window_minutes=120` | Array with `status` ∈ healthy/warning/critical/stale/unknown |
| `GET /api/v1/devices/{device_id}` | `404` if unknown |
| `GET /api/v1/devices/{device_id}/telemetry?window_minutes=120&limit=2000` | Raw samples, `404` if unknown |
| `GET /api/v1/devices/{device_id}/logs?window_minutes=120&limit=200` | Newest first |

## Metrics

### `GET /api/v1/metrics/overview?window_minutes=120`

```json
{
  "total_devices": 8,
  "online_devices": 7,
  "avg_latency_ms": 41.2,
  "avg_packet_loss_pct": 1.83,
  "avg_api_latency_ms": 418.7,
  "avg_cpu_pct": 44.1,
  "avg_memory_pct": 58.9,
  "api_error_rate_pct": 4.21,
  "sample_count": 2888,
  "incident_count": 6,
  "network_health_score": 82.4
}
```

### `GET /api/v1/metrics/timeseries?window_minutes=120[&device_id=GW-002]`

One-minute buckets: `bucket`, `avg_latency_ms`, `avg_packet_loss_pct`,
`avg_api_latency_ms`, `avg_cpu_pct`, `avg_memory_pct`, `api_error_rate_pct`.

### `GET /api/v1/metrics/root-causes?window_minutes=1440`

`root_cause`, `category`, `incidents`, `avg_confidence`, `last_seen`.

## Diagnostics

### `POST /api/v1/diagnostics/run?window_minutes=120[&device_id=GW-002]`

Runs the classifier over stored logs + telemetry, persists every non-healthy
diagnosis and returns one result per device:

```json
{
  "device_id": "GW-002",
  "root_cause": "DNS resolution failure",
  "category": "dns",
  "confidence": 0.99,
  "severity": "critical",
  "evidence": ["DNS_RESOLVE_FAILED x71", "TELEMETRY_DNS_LATENCY x1"],
  "recommended_action": "Validate DNS servers, clear the resolver cache...",
  "window_start": "2026-11-09T10:00:00Z",
  "window_end": "2026-11-09T12:00:00Z"
}
```

### `GET /api/v1/diagnostics?window_minutes=1440&limit=50`

Stored incidents, newest first. `evidence` is returned as a JSON array.

## Status codes

| Code | When |
|---|---|
| 200 | Successful read or classifier run |
| 201 | Telemetry or log accepted |
| 404 | Unknown device |
| 422 | Schema violation, malformed log line, out-of-range query parameter |
