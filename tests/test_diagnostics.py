from __future__ import annotations

import pytest

from gateway_telemetry.diagnostics import TelemetrySnapshot, diagnose_device
from gateway_telemetry.log_parser import parse_line

WINDOW_START = "2026-11-09T10:00:00Z"
WINDOW_END = "2026-11-09T12:00:00Z"


def _logs(*messages: str, severity: str = "ERROR") -> list:
    return [
        parse_line(f"2026-11-09T11:00:00Z [{severity}] test: {message}")
        for message in messages
    ]


def test_dns_logs_classify_as_dns_failure() -> None:
    diagnosis = diagnose_device(
        "GW-001",
        _logs("DNS resolution failed for api.vendor.net (SERVFAIL)"),
        TelemetrySnapshot(samples=10, wan_online_ratio=1.0),
        WINDOW_START,
        WINDOW_END,
    )

    assert diagnosis.root_cause == "DNS resolution failure"
    assert diagnosis.category == "dns"
    assert diagnosis.severity == "warning"
    assert 0.0 < diagnosis.confidence <= 0.99
    assert any("DNS_RESOLVE_FAILED" in item for item in diagnosis.evidence)


def test_wan_offline_telemetry_classifies_uplink_loss() -> None:
    diagnosis = diagnose_device(
        "GW-002",
        [],
        TelemetrySnapshot(
            samples=20,
            wan_online_ratio=0.2,
            avg_packet_loss_pct=25.0,
        ),
        WINDOW_START,
        WINDOW_END,
    )

    assert diagnosis.root_cause == "WAN uplink loss"
    assert diagnosis.category == "wan"
    assert diagnosis.severity == "critical"
    assert "TELEMETRY_WAN_OFFLINE" in diagnosis.evidence_counts


def test_api_latency_classifies_backend_degradation() -> None:
    diagnosis = diagnose_device(
        "GW-003",
        [],
        TelemetrySnapshot(
            samples=20,
            wan_online_ratio=1.0,
            avg_api_latency_ms=3200.0,
            api_error_ratio=0.35,
        ),
        WINDOW_START,
        WINDOW_END,
    )

    assert diagnosis.root_cause == "Backend API degradation"
    assert diagnosis.category == "backend"


def test_memory_pressure_classifies_resource_exhaustion() -> None:
    diagnosis = diagnose_device(
        "GW-004",
        [],
        TelemetrySnapshot(samples=20, wan_online_ratio=1.0, avg_memory_pct=96.0),
        WINDOW_START,
        WINDOW_END,
    )

    assert diagnosis.root_cause == "Resource exhaustion"
    assert diagnosis.category == "resource"


def test_healthy_device_has_no_fault() -> None:
    diagnosis = diagnose_device(
        "GW-005",
        _logs("heartbeat ok", severity="INFO"),
        TelemetrySnapshot(samples=20, wan_online_ratio=1.0),
        WINDOW_START,
        WINDOW_END,
    )

    assert diagnosis.root_cause == "No fault detected"
    assert diagnosis.category == "healthy"
    assert diagnosis.confidence == 0.0
    assert diagnosis.evidence == []


def test_dominant_cause_wins_when_evidence_competes() -> None:
    logs = _logs(
        "Kernel panic - not syncing: watchdog reset",
        "Kernel panic - not syncing: watchdog reset",
        "DNS resolution failed for api.vendor.net",
    )

    diagnosis = diagnose_device(
        "GW-006",
        logs,
        TelemetrySnapshot(samples=20, wan_online_ratio=1.0),
        WINDOW_START,
        WINDOW_END,
    )

    assert diagnosis.root_cause == "Firmware crash/reboot"
    assert diagnosis.confidence > 0.5


@pytest.mark.parametrize(
    "snapshot",
    [
        TelemetrySnapshot(),
        TelemetrySnapshot(samples=5),
        TelemetrySnapshot(samples=5, wan_online_ratio=1.0, avg_cpu_pct=99, avg_memory_pct=99),
    ],
)
def test_confidence_and_severity_are_bounded(snapshot: TelemetrySnapshot) -> None:
    diagnosis = diagnose_device("GW-007", [], snapshot, WINDOW_START, WINDOW_END)

    assert 0.0 <= diagnosis.confidence <= 0.99
    assert diagnosis.severity in {"healthy", "info", "warning", "critical"}
