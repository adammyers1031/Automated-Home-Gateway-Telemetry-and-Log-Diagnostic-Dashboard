"""Rule-based root-cause classification.

Evidence comes from two places:

* parsed log lines with matched failure signatures, weighted by signature
  weight and log severity;
* telemetry aggregates (WAN state, packet loss, resource pressure, API
  errors, DNS latency, Wi-Fi quality).

The root cause with the highest score wins. Confidence reflects both how
dominant that cause is and how much evidence supports it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .log_parser import ParsedLog

SEVERITY_MULTIPLIER = {
    "DEBUG": 0.1,
    "INFO": 0.2,
    "WARN": 1.0,
    "ERROR": 2.0,
    "CRITICAL": 3.0,
}

TELEMETRY_ACTION = {
    "WAN uplink loss": "Check WAN cabling/ONT, line status and ISP outage notifications.",
    "Resource exhaustion": "Inspect CPU/memory pressure and restart the offending service.",
    "Backend API degradation": "Inspect backend latency/error budgets and retry with backoff.",
    "DNS resolution failure": "Validate DNS servers and clear the resolver cache.",
    "Wi-Fi client instability": "Review channel utilisation, client RSSI and roaming thresholds.",
}


@dataclass
class TelemetrySnapshot:
    """Window aggregates for one device, produced by SQL."""

    samples: int = 0
    wan_online_ratio: float = 0.0
    avg_packet_loss_pct: float = 0.0
    avg_cpu_pct: float = 0.0
    avg_memory_pct: float = 0.0
    avg_api_latency_ms: float = 0.0
    api_error_ratio: float = 0.0
    avg_dns_lookup_ms: float = 0.0
    avg_wifi_rssi: float = 0.0
    avg_wifi_clients: float = 0.0

    @property
    def wan_offline_ratio(self) -> float:
        return max(0.0, 1.0 - self.wan_online_ratio)


@dataclass
class Diagnosis:
    device_id: str
    root_cause: str
    category: str
    confidence: float
    severity: str
    evidence: list[str]
    recommended_action: str
    window_start: str
    window_end: str
    score: float = 0.0
    evidence_counts: dict[str, int] = field(default_factory=dict)


def _telemetry_evidence(
    scores: defaultdict[str, float],
    counts: defaultdict[str, Counter[str]],
    actions: dict[str, str],
    snapshot: TelemetrySnapshot,
) -> None:
    """Fold telemetry anomalies into the same evidence model as logs."""

    def add(cause: str, weight: float, code: str) -> None:
        scores[cause] += weight
        counts[cause][code] += 1
        actions.setdefault(cause, TELEMETRY_ACTION.get(cause, "Investigate the affected device."))

    if snapshot.samples == 0:
        return

    if snapshot.wan_offline_ratio >= 0.5:
        add("WAN uplink loss", 6.0, "TELEMETRY_WAN_OFFLINE")
    if snapshot.avg_packet_loss_pct >= 8.0:
        add(
            "WAN uplink loss",
            min(4.0, snapshot.avg_packet_loss_pct / 5.0),
            "TELEMETRY_PACKET_LOSS",
        )

    if snapshot.avg_memory_pct >= 90.0:
        add(
            "Resource exhaustion",
            min(5.0, (snapshot.avg_memory_pct - 85.0) / 3.0),
            "TELEMETRY_MEMORY_PRESSURE",
        )
    if snapshot.avg_cpu_pct >= 90.0:
        add(
            "Resource exhaustion",
            min(4.0, (snapshot.avg_cpu_pct - 85.0) / 3.0),
            "TELEMETRY_CPU_PRESSURE",
        )

    if snapshot.avg_api_latency_ms >= 1000.0:
        add(
            "Backend API degradation",
            min(4.0, snapshot.avg_api_latency_ms / 1000.0),
            "TELEMETRY_API_LATENCY",
        )
    if snapshot.api_error_ratio >= 0.10:
        add(
            "Backend API degradation",
            min(4.0, snapshot.api_error_ratio * 10.0),
            "TELEMETRY_API_ERRORS",
        )

    if snapshot.avg_dns_lookup_ms >= 500.0:
        add(
            "DNS resolution failure",
            min(4.0, snapshot.avg_dns_lookup_ms / 500.0),
            "TELEMETRY_DNS_LATENCY",
        )

    if snapshot.avg_wifi_rssi <= -70.0 and snapshot.avg_wifi_clients >= 25:
        add("Wi-Fi client instability", 2.0, "TELEMETRY_WIFI_QUALITY")


def diagnose_device(
    device_id: str,
    logs: list[ParsedLog],
    snapshot: TelemetrySnapshot,
    window_start: str,
    window_end: str,
) -> Diagnosis:
    """Classify the most likely root cause for one device."""
    scores: defaultdict[str, float] = defaultdict(float)
    counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    actions: dict[str, str] = {}
    severities: list[str] = []

    for log in logs:
        signature = log.signature
        if signature is None:
            continue
        multiplier = SEVERITY_MULTIPLIER.get(log.severity, 0.5)
        scores[signature.root_cause] += signature.weight * multiplier
        counts[signature.root_cause][signature.code] += 1
        actions.setdefault(signature.root_cause, signature.action)
        severities.append(log.severity)

    _telemetry_evidence(scores, counts, actions, snapshot)

    if not scores:
        return Diagnosis(
            device_id=device_id,
            root_cause="No fault detected",
            category="healthy",
            confidence=0.0,
            severity="healthy",
            evidence=[],
            recommended_action="No action required.",
            window_start=window_start,
            window_end=window_end,
        )

    root_cause, score = max(scores.items(), key=lambda item: item[1])
    total = sum(scores.values())
    share = score / total if total else 0.0
    volume = min(1.0, score / 5.0)
    confidence = round(min(0.99, (0.55 + 0.45 * share) * (0.5 + 0.5 * volume)), 3)

    category = "unknown"
    for log in logs:
        if log.signature and log.signature.root_cause == root_cause:
            category = log.signature.category
            break
    else:
        category = {
            "WAN uplink loss": "wan",
            "Resource exhaustion": "resource",
            "Backend API degradation": "backend",
            "DNS resolution failure": "dns",
            "Wi-Fi client instability": "wifi",
        }.get(root_cause, "unknown")

    if "CRITICAL" in severities or score >= 10.0:
        severity = "critical"
    elif "ERROR" in severities or score >= 4.0:
        severity = "warning"
    else:
        severity = "info"

    evidence = [
        f"{code} x{count}" for code, count in counts[root_cause].most_common(5)
    ]

    return Diagnosis(
        device_id=device_id,
        root_cause=root_cause,
        category=category,
        confidence=confidence,
        severity=severity,
        evidence=evidence,
        recommended_action=actions.get(root_cause, "Investigate the affected device."),
        window_start=window_start,
        window_end=window_end,
        score=round(score, 3),
        evidence_counts=dict(counts[root_cause]),
    )
