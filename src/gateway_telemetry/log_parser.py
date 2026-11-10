"""Syslog parsing and network failure-signature extraction.

A raw line looks like::

    2026-11-09T12:00:00Z [ERROR] dhcp: DHCP discover timeout - no offer received

The parser extracts structured fields and matches the message against the
signature library. Each signature maps to a diagnostic root cause and a
remediation action used by the classifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s+"
    r"\[(?P<severity>DEBUG|INFO|WARN|ERROR|CRITICAL)\]\s+"
    r"(?P<component>[A-Za-z0-9_.-]+):\s+"
    r"(?P<message>.+)$"
)


class LogParseError(ValueError):
    """Raised when a log line does not match the expected syslog format."""


@dataclass(frozen=True)
class Signature:
    code: str
    root_cause: str
    category: str
    pattern: re.Pattern[str]
    weight: float
    action: str


@dataclass(frozen=True)
class ParsedLog:
    ts: str
    severity: str
    component: str
    message: str
    raw: str
    signature: Signature | None

    @property
    def code(self) -> str | None:
        return self.signature.code if self.signature else None

    @property
    def category(self) -> str | None:
        return self.signature.category if self.signature else None


# Ordered most-specific first: the first match wins.
SIGNATURES: tuple[Signature, ...] = (
    Signature(
        code="PPPOE_AUTH_FAILED",
        root_cause="PPPoE authentication failure",
        category="wan",
        pattern=re.compile(r"pppoe.*(auth|authentication).*(fail|reject)|chap.*auth.*fail", re.I),
        weight=4.5,
        action=(
            "Verify ISP credentials and re-run the PPPoE session; "
            "escalate to the ISP if it persists."
        ),
    ),
    Signature(
        code="DHCP_TIMEOUT",
        root_cause="DHCP lease acquisition failure",
        category="wan",
        pattern=re.compile(r"dhcp.*(timeout|no offer|no response|discover)|no dhcp lease", re.I),
        weight=3.5,
        action="Release/renew the WAN lease, check the ISP handoff and DHCP relay configuration.",
    ),
    Signature(
        code="WAN_LINK_DOWN",
        root_cause="WAN uplink loss",
        category="wan",
        pattern=re.compile(
            r"wan.*(link|interface|carrier).*(down|lost)|link down|no carrier", re.I
        ),
        weight=5.0,
        action="Check WAN cabling/ONT, line status and ISP outage notifications.",
    ),
    Signature(
        code="DNS_RESOLVE_FAILED",
        root_cause="DNS resolution failure",
        category="dns",
        pattern=re.compile(
            r"dns.*(fail|timeout|servfail|nxdomain|unreachable)|resolver.*(fail|timeout)", re.I
        ),
        weight=3.0,
        action=(
            "Validate DNS servers, clear the resolver cache and fall back to a "
            "secondary resolver."
        ),
    ),
    Signature(
        code="TLS_HANDSHAKE_FAILED",
        root_cause="TLS handshake failure",
        category="security",
        pattern=re.compile(r"tls.*(handshake|fail)|certificate.*(expired|invalid|untrusted)", re.I),
        weight=3.0,
        action="Check the device trust store, certificate expiry and outbound 443 reachability.",
    ),
    Signature(
        code="API_TIMEOUT",
        root_cause="Backend API degradation",
        category="backend",
        pattern=re.compile(r"(api|backend|upstream).*(timeout|timed out)|request.*timed out", re.I),
        weight=3.0,
        action="Inspect backend latency/error budgets and retry with exponential backoff.",
    ),
    Signature(
        code="API_5XX",
        root_cause="Backend API degradation",
        category="backend",
        pattern=re.compile(r"(http|api|backend).*\b5\d{2}\b|upstream.*(error|unavailable)", re.I),
        weight=3.0,
        action="Inspect backend latency/error budgets and retry with exponential backoff.",
    ),
    Signature(
        code="WIFI_DEAUTH_FLOOD",
        root_cause="Wi-Fi client instability",
        category="wifi",
        pattern=re.compile(r"deauth|disassoc|wifi.*(flap|instability|retry)", re.I),
        weight=2.0,
        action="Review channel utilisation, client RSSI and roaming thresholds.",
    ),
    Signature(
        code="CONNTRACK_FULL",
        root_cause="Resource exhaustion",
        category="resource",
        pattern=re.compile(r"conntrack.*(full|overflow)|nf_conntrack.*full", re.I),
        weight=3.5,
        action="Reduce connection churn, tune conntrack limits and check for a traffic storm.",
    ),
    Signature(
        code="MEMORY_PRESSURE",
        root_cause="Resource exhaustion",
        category="resource",
        pattern=re.compile(r"out of memory|\boom\b|memory pressure|low memory", re.I),
        weight=4.0,
        action=(
            "Capture a memory profile, restart the leaking service and schedule a "
            "firmware review."
        ),
    ),
    Signature(
        code="FIRMWARE_CRASH",
        root_cause="Firmware crash/reboot",
        category="stability",
        pattern=re.compile(
            r"kernel panic|firmware.*(crash|reboot)|watchdog reset|segfault", re.I
        ),
        weight=5.0,
        action="Collect crash dumps, roll back the last firmware change and open a vendor ticket.",
    ),
    Signature(
        code="NTP_SYNC_FAILED",
        root_cause="NTP synchronization failure",
        category="time",
        pattern=re.compile(
            r"ntp.*(fail|unreachable|not synchronized|no server)|time sync.*fail", re.I
        ),
        weight=1.5,
        action="Check UDP/123 egress and configure a reachable NTP pool.",
    ),
)


def _match_signature(message: str) -> Signature | None:
    for signature in SIGNATURES:
        if signature.pattern.search(message):
            return signature
    return None


def parse_line(line: str) -> ParsedLog:
    """Parse one syslog line and attach the matching failure signature."""
    match = LINE_RE.match(line.strip())
    if not match:
        raise LogParseError(
            "Line does not match '<ISO timestamp> [SEVERITY] component: message'"
        )

    message = match.group("message").strip()
    return ParsedLog(
        ts=match.group("ts"),
        severity=match.group("severity"),
        component=match.group("component"),
        message=message,
        raw=line.strip(),
        signature=_match_signature(message),
    )


def parse_lines(lines: list[str]) -> list[ParsedLog]:
    return [parse_line(line) for line in lines]
