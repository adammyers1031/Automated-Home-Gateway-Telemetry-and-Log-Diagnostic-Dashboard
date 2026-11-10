from __future__ import annotations

import pytest

from gateway_telemetry.log_parser import LogParseError, parse_line


def test_parse_structured_fields() -> None:
    parsed = parse_line(
        "2026-11-09T12:00:00Z [ERROR] dns: DNS resolution failed for api.vendor.net"
    )

    assert parsed.ts == "2026-11-09T12:00:00Z"
    assert parsed.severity == "ERROR"
    assert parsed.component == "dns"
    assert parsed.message == "DNS resolution failed for api.vendor.net"
    assert parsed.signature is not None
    assert parsed.code == "DNS_RESOLVE_FAILED"
    assert parsed.category == "dns"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("WAN link down: carrier lost on eth0", "WAN_LINK_DOWN"),
        ("DHCP discover timeout - no offer received", "DHCP_TIMEOUT"),
        ("PPPoE authentication failed: CHAP rejected", "PPPOE_AUTH_FAILED"),
        ("Out of memory: killed process telemetry-agent", "MEMORY_PRESSURE"),
        ("nf_conntrack table full - dropping connections", "CONNTRACK_FULL"),
        ("Backend returned HTTP 503 upstream unavailable", "API_5XX"),
        ("Backend API request timed out after 3000ms", "API_TIMEOUT"),
        ("Kernel panic - not syncing: watchdog reset", "FIRMWARE_CRASH"),
        ("Deauth flood detected on radio0", "WIFI_DEAUTH_FLOOD"),
    ],
)
def test_signature_extraction(line: str, expected: str) -> None:
    parsed = parse_line(f"2026-11-09T12:00:00Z [ERROR] test: {line}")

    assert parsed.signature is not None
    assert parsed.code == expected


def test_healthy_logs_have_no_signature() -> None:
    healthy_messages = (
        "heartbeat ok",
        "ntp sync ok",
        "dhcp lease renewed",
        "resolver cache refreshed",
    )
    for message in healthy_messages:
        parsed = parse_line(f"2026-11-09T12:00:00Z [INFO] system: {message}")
        assert parsed.signature is None, message


def test_ntp_failure_is_detected() -> None:
    parsed = parse_line("2026-11-09T12:00:00Z [WARN] ntp: ntp sync failed against pool.ntp.org")

    assert parsed.code == "NTP_SYNC_FAILED"
    assert parsed.category == "time"


@pytest.mark.parametrize(
    "line",
    [
        "not a log line",
        "2026-11-09 12:00:00 [ERROR] dns: missing T and Z",
        "2026-11-09T12:00:00Z [BOGUS] dns: unknown severity",
        "2026-11-09T12:00:00Z [ERROR] missing-colon",
    ],
)
def test_malformed_lines_raise(line: str) -> None:
    with pytest.raises(LogParseError):
        parse_line(line)
