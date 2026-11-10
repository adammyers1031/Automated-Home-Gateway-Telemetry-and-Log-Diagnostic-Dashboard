PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS devices (
    device_id       TEXT PRIMARY KEY,
    hardware_model  TEXT NOT NULL,
    firmware        TEXT NOT NULL,
    region          TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    ts              TEXT NOT NULL,
    uptime_s        INTEGER NOT NULL,
    cpu_pct         REAL NOT NULL,
    memory_pct      REAL NOT NULL,
    wan_status      TEXT NOT NULL CHECK (wan_status IN ('online', 'offline', 'degraded')),
    wan_ip          TEXT,
    latency_ms      REAL,
    packet_loss_pct REAL NOT NULL,
    dns_lookup_ms   REAL,
    dhcp_lease_s    INTEGER,
    wifi_clients    INTEGER NOT NULL,
    wifi_rssi_avg   REAL,
    tx_bytes        INTEGER NOT NULL,
    rx_bytes        INTEGER NOT NULL,
    api_latency_ms  REAL,
    http_status     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_telemetry_device_ts ON telemetry(device_id, ts);
CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(ts);

CREATE TABLE IF NOT EXISTS device_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    ts          TEXT NOT NULL,
    severity    TEXT NOT NULL,
    component   TEXT NOT NULL,
    message     TEXT NOT NULL,
    signature   TEXT,
    category    TEXT,
    raw         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_logs_device_ts ON device_logs(device_id, ts);
CREATE INDEX IF NOT EXISTS idx_logs_signature ON device_logs(signature);

CREATE TABLE IF NOT EXISTS diagnoses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id           TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    created_at          TEXT NOT NULL,
    window_start        TEXT NOT NULL,
    window_end          TEXT NOT NULL,
    root_cause          TEXT NOT NULL,
    category            TEXT NOT NULL,
    confidence          REAL NOT NULL,
    severity            TEXT NOT NULL,
    evidence            TEXT NOT NULL,
    recommended_action  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_diagnoses_device_created ON diagnoses(device_id, created_at);
CREATE INDEX IF NOT EXISTS idx_diagnoses_created ON diagnoses(created_at);
