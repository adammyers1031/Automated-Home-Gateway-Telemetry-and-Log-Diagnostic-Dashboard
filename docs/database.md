# Database model

SQLite file, default `data/telemetry.db`. WAL mode and foreign keys are enabled
per connection.

## Tables

### `devices`

| Column | Type | Notes |
|---|---|---|
| `device_id` | TEXT PK | e.g. `GW-001` |
| `hardware_model` | TEXT | `HG-5000`, `HG-7000`, `HG-9000` |
| `firmware` | TEXT | e.g. `2.4.1` |
| `region` | TEXT | e.g. `us-east` |
| `created_at` | TEXT | UTC ISO |

### `telemetry` (one row per heartbeat)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `device_id` | TEXT FK → devices | cascade delete |
| `ts` | TEXT | UTC ISO, indexed with device |
| `uptime_s` | INTEGER | |
| `cpu_pct`, `memory_pct` | REAL | 0–100 |
| `wan_status` | TEXT | checked enum |
| `wan_ip` | TEXT | null when offline |
| `latency_ms` | REAL | nullable for offline WAN |
| `packet_loss_pct` | REAL | |
| `dns_lookup_ms` | REAL | |
| `dhcp_lease_s` | INTEGER | |
| `wifi_clients` | INTEGER | |
| `wifi_rssi_avg` | REAL | dBm |
| `tx_bytes`, `rx_bytes` | INTEGER | cumulative counters |
| `api_latency_ms` | REAL | |
| `http_status` | INTEGER | 100–599 |

Indexes: `(device_id, ts)`, `(ts)`.

### `device_logs`

`id`, `device_id`, `ts`, `severity`, `component`, `message`, `signature`,
`category`, `raw`. Indexed on `(device_id, ts)` and `signature`.

### `diagnoses`

`id`, `device_id`, `created_at`, window bounds, `root_cause`, `category`,
`confidence`, `severity`, `evidence` (JSON array), `recommended_action`.
Indexed on `(device_id, created_at)` and `(created_at)`.

## Health score

```
health = 100
       − 40 × (1 − online_devices / total_devices)
       − min(25, avg_packet_loss_pct × 5)
       − min(20, avg_latency_ms / 20)
       − min(15, api_error_rate_pct)
```

Clamped to 0–100. Returns 0 when the window has no samples. "Online devices"
counts the latest sample per device in the window, so a gateway that stopped
reporting is penalised.

## Why these indexes

Every dashboard query filters on a time window and optionally a device. The
composite `(device_id, ts)` index serves device drill-downs; the `ts` index
serves fleet-wide windows. The `signature` index supports failure-signature
analysis without scanning log text.
