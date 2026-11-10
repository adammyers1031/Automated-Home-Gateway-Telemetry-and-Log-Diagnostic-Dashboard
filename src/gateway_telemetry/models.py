"""Pydantic request schemas.

These are the payload contracts validated at the API boundary. Malformed or
out-of-range telemetry is rejected with HTTP 422 before it reaches SQLite.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WanStatus = Literal["online", "offline", "degraded"]
Severity = Literal["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]

DEVICE_ID_PATTERN = r"^[A-Za-z0-9._-]+$"


class TelemetryIn(BaseModel):
    """One device-to-backend telemetry heartbeat."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=3, max_length=64, pattern=DEVICE_ID_PATTERN)
    ts: datetime | None = Field(default=None, description="Defaults to ingest time (UTC)")
    uptime_s: int = Field(ge=0, le=10**9)
    cpu_pct: float = Field(ge=0, le=100)
    memory_pct: float = Field(ge=0, le=100)
    wan_status: WanStatus
    wan_ip: str | None = Field(default=None, max_length=64)
    latency_ms: float | None = Field(default=None, ge=0, le=60_000)
    packet_loss_pct: float = Field(ge=0, le=100)
    dns_lookup_ms: float | None = Field(default=None, ge=0, le=60_000)
    dhcp_lease_s: int | None = Field(default=None, ge=0)
    wifi_clients: int = Field(ge=0, le=10_000)
    wifi_rssi_avg: float | None = Field(default=None, ge=-100, le=0)
    tx_bytes: int = Field(ge=0)
    rx_bytes: int = Field(ge=0)
    api_latency_ms: float | None = Field(default=None, ge=0, le=60_000)
    http_status: int = Field(ge=100, le=599)

    # Optional device metadata; unknown devices are auto-registered on ingest.
    hardware_model: str | None = Field(default=None, max_length=64)
    firmware: str | None = Field(default=None, max_length=32)
    region: str | None = Field(default=None, max_length=32)


class LogLineIn(BaseModel):
    """A raw syslog-style line emitted by a gateway."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=3, max_length=64, pattern=DEVICE_ID_PATTERN)
    line: str = Field(
        min_length=10,
        max_length=4000,
        description="Example: 2026-11-09T12:00:00Z [ERROR] dns: DNS resolution failed",
    )


class TelemetryAccepted(BaseModel):
    id: int
    device_id: str
    ts: str
    status: str = "accepted"


class LogAccepted(BaseModel):
    id: int
    device_id: str
    ts: str
    severity: str
    component: str
    message: str
    signature: str | None = None
    category: str | None = None


class DiagnosisOut(BaseModel):
    device_id: str
    root_cause: str
    category: str
    confidence: float
    severity: str
    evidence: list[str]
    recommended_action: str
    window_start: str
    window_end: str
