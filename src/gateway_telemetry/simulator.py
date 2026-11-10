"""Deterministic home-gateway telemetry and log simulator.

Each device is assigned a fault profile (DNS, WAN, memory, API, Wi-Fi or
firmware). The fault activates part-way through the simulated window, so a
dashboard seeded from the generator shows both healthy and degraded periods.

The generator is pure: given the same devices, window and seed it produces
byte-identical output, which keeps tests and demos reproducible.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from .timeutil import to_iso

PROFILES = ("healthy", "dns", "wan", "memory", "api", "wifi", "firmware", "healthy")

FAULT_LOGS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "dns": (
        ("WARN", "dns", "DNS resolution failed for api.vendor.net (SERVFAIL)"),
        ("ERROR", "dns", "DNS lookup timeout after 2000ms for telemetry.vendor.net"),
    ),
    "wan": (
        ("ERROR", "wan", "WAN link down: carrier lost on eth0"),
        ("WARN", "dhcp", "DHCP discover timeout - no offer received"),
        ("ERROR", "pppoe", "PPPoE authentication failed: CHAP rejected"),
    ),
    "memory": (
        ("WARN", "system", "memory pressure: available memory below 5%"),
        ("ERROR", "kernel", "Out of memory: killed process telemetry-agent"),
        ("ERROR", "nat", "nf_conntrack table full - dropping new connections"),
    ),
    "api": (
        ("WARN", "api", "Backend API request timed out after 3000ms"),
        ("ERROR", "api", "Backend returned HTTP 503 upstream unavailable"),
    ),
    "wifi": (
        ("WARN", "wifi", "Deauth flood detected on radio0 (38 events/60s)"),
        ("WARN", "wifi", "Wi-Fi client instability: repeated disassoc for client"),
    ),
    "firmware": (
        ("CRITICAL", "kernel", "Kernel panic - not syncing: watchdog reset"),
        ("ERROR", "firmware", "Firmware crash detected, rebooting gateway"),
    ),
}

HEALTHY_LOGS: tuple[tuple[str, str], ...] = (
    ("system", "heartbeat ok"),
    ("dhcp", "dhcp lease renewed"),
    ("ntp", "ntp sync ok"),
    ("dns", "resolver cache refreshed"),
)


@dataclass(frozen=True)
class SimDevice:
    device_id: str
    hardware_model: str
    firmware: str
    region: str
    profile: str
    management_ip: str


@dataclass(frozen=True)
class TelemetrySample:
    device_id: str
    ts: str
    uptime_s: int
    cpu_pct: float
    memory_pct: float
    wan_status: str
    wan_ip: str | None
    latency_ms: float | None
    packet_loss_pct: float
    dns_lookup_ms: float | None
    dhcp_lease_s: int | None
    wifi_clients: int
    wifi_rssi_avg: float | None
    tx_bytes: int
    rx_bytes: int
    api_latency_ms: float | None
    http_status: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LogSample:
    device_id: str
    ts: str
    severity: str
    component: str
    message: str

    @property
    def raw(self) -> str:
        return f"{self.ts} [{self.severity}] {self.component}: {self.message}"


def build_devices(count: int = 8, region: str = "us-east") -> list[SimDevice]:
    """Build a deterministic fleets of simulated gateways."""
    models = ("HG-5000", "HG-7000", "HG-9000")
    firmwares = ("2.4.1", "2.5.0", "3.0.2")
    devices: list[SimDevice] = []
    for index in range(max(1, count)):
        number = index + 1
        devices.append(
            SimDevice(
                device_id=f"GW-{number:03d}",
                hardware_model=models[index % len(models)],
                firmware=firmwares[index % len(firmwares)],
                region=region,
                profile=PROFILES[index % len(PROFILES)],
                management_ip=f"192.0.2.{10 + index % 200}",
            )
        )
    return devices


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _build_sample(
    device: SimDevice,
    ts: datetime,
    active: bool,
    rebooted: bool,
    rng: random.Random,
    boot_time: datetime,
    tx_total: int,
    rx_total: int,
) -> tuple[TelemetrySample, list[LogSample]]:
    profile = device.profile
    ts_iso = to_iso(ts)

    cpu = _clamp(rng.gauss(32.0, 8.0), 2.0, 99.0)
    memory = _clamp(rng.gauss(48.0, 9.0), 5.0, 99.0)
    wan_status = "online"
    latency: float | None = _clamp(rng.gauss(16.0, 5.0), 1.0, 2000.0)
    packet_loss = _clamp(rng.gauss(0.2, 0.2), 0.0, 100.0)
    dns_lookup: float | None = _clamp(rng.gauss(12.0, 5.0), 1.0, 60_000.0)
    api_latency: float | None = _clamp(rng.gauss(180.0, 50.0), 20.0, 60_000.0)
    http_status = 200
    wifi_clients = int(_clamp(rng.gauss(12.0, 5.0), 0, 100))
    wifi_rssi = _clamp(rng.gauss(-58.0, 7.0), -100.0, -25.0)

    if active and profile == "dns":
        dns_lookup = _clamp(rng.gauss(1800.0, 600.0), 500.0, 6000.0)
        latency = _clamp(rng.gauss(110.0, 40.0), 20.0, 2000.0)
        api_latency = _clamp(rng.gauss(1400.0, 500.0), 200.0, 20_000.0)
        if rng.random() < 0.08:
            http_status = 504
    elif active and profile == "wan":
        if rng.random() < 0.65:
            wan_status = "offline"
            latency = None
            packet_loss = _clamp(rng.gauss(25.0, 10.0), 5.0, 100.0)
        else:
            wan_status = "degraded"
            latency = _clamp(rng.gauss(220.0, 80.0), 30.0, 5000.0)
            packet_loss = _clamp(rng.gauss(12.0, 6.0), 2.0, 80.0)
        dns_lookup = _clamp(rng.gauss(900.0, 300.0), 100.0, 8000.0)
        api_latency = _clamp(rng.gauss(1200.0, 400.0), 200.0, 20_000.0)
        if rng.random() < 0.15:
            http_status = 503
    elif active and profile == "memory":
        memory = _clamp(rng.gauss(96.0, 2.0), 88.0, 99.5)
        cpu = _clamp(rng.gauss(82.0, 8.0), 50.0, 99.0)
        latency = _clamp(rng.gauss(60.0, 20.0), 15.0, 5000.0)
        api_latency = _clamp(rng.gauss(700.0, 250.0), 200.0, 20_000.0)
        if rng.random() < 0.10:
            http_status = 500
    elif active and profile == "api":
        api_latency = _clamp(rng.gauss(2800.0, 700.0), 1000.0, 60_000.0)
        if rng.random() < 0.35:
            http_status = rng.choice([500, 502, 503])
        latency = _clamp(rng.gauss(45.0, 15.0), 10.0, 2000.0)
        cpu = _clamp(rng.gauss(55.0, 10.0), 10.0, 99.0)
    elif active and profile == "wifi":
        wifi_clients = int(_clamp(rng.gauss(42.0, 8.0), 25, 100))
        wifi_rssi = _clamp(rng.gauss(-76.0, 5.0), -100.0, -55.0)
        latency = _clamp(rng.gauss(45.0, 15.0), 10.0, 2000.0)
        packet_loss = _clamp(rng.gauss(1.8, 0.8), 0.5, 40.0)
    elif active and profile == "firmware":
        cpu = _clamp(rng.gauss(88.0, 7.0), 55.0, 99.0)
        memory = _clamp(rng.gauss(72.0, 8.0), 40.0, 99.0)
        latency = _clamp(rng.gauss(70.0, 25.0), 15.0, 5000.0)
        packet_loss = _clamp(rng.gauss(2.5, 1.2), 0.2, 40.0)

    log_events: list[LogSample] = []
    if active and profile in FAULT_LOGS and rng.random() < 0.30:
        severity, component, message = rng.choice(FAULT_LOGS[profile])
        log_events.append(
            LogSample(device.device_id, ts_iso, severity, component, message)
        )
    elif not active and rng.random() < 0.015:
        component, message = rng.choice(HEALTHY_LOGS)
        log_events.append(LogSample(device.device_id, ts_iso, "INFO", component, message))

    if rebooted:
        log_events.append(
            LogSample(
                device.device_id,
                ts_iso,
                "CRITICAL",
                "kernel",
                "Kernel panic - not syncing: watchdog reset",
            )
        )

    uptime_s = max(0, int((ts - boot_time).total_seconds()))
    tx_total += rng.randint(1_000, 50_000)
    rx_total += rng.randint(5_000, 150_000)

    sample = TelemetrySample(
        device_id=device.device_id,
        ts=ts_iso,
        uptime_s=uptime_s,
        cpu_pct=round(cpu, 2),
        memory_pct=round(memory, 2),
        wan_status=wan_status,
        wan_ip=device.management_ip if wan_status != "offline" else None,
        latency_ms=round(latency, 2) if latency is not None else None,
        packet_loss_pct=round(packet_loss, 3),
        dns_lookup_ms=round(dns_lookup, 2) if dns_lookup is not None else None,
        dhcp_lease_s=rng.randint(1800, 86_400) if wan_status != "offline" else 0,
        wifi_clients=wifi_clients,
        wifi_rssi_avg=round(wifi_rssi, 2),
        tx_bytes=tx_total,
        rx_bytes=rx_total,
        api_latency_ms=round(api_latency, 2) if api_latency is not None else None,
        http_status=http_status,
    )
    return sample, log_events


def generate_batch(
    devices: list[SimDevice],
    start: datetime,
    end: datetime,
    interval_s: int = 60,
    seed: int = 42,
) -> tuple[list[TelemetrySample], list[LogSample]]:
    """Generate telemetry and logs for every device between ``start`` and ``end``."""
    if interval_s < 1:
        raise ValueError("interval_s must be >= 1")
    if end < start:
        raise ValueError("end must be after start")

    telemetry: list[TelemetrySample] = []
    logs: list[LogSample] = []
    span = end - start
    fault_start = start + span * 0.4

    for index, device in enumerate(devices):
        rng = random.Random(seed + index * 7919)
        boot_time = start - timedelta(seconds=rng.randint(3_600, 172_800))
        tx_total = rng.randint(10_000_000, 500_000_000)
        rx_total = rng.randint(50_000_000, 2_000_000_000)

        ts = start
        while ts <= end:
            active = device.profile != "healthy" and ts >= fault_start
            rebooted = False

            if active and device.profile in {"firmware", "memory"}:
                chance = 0.04 if device.profile == "firmware" else 0.01
                if rng.random() < chance:
                    rebooted = True
                    boot_time = ts

            sample, events = _build_sample(
                device,
                ts,
                active,
                rebooted,
                rng,
                boot_time,
                tx_total,
                rx_total,
            )
            tx_total = sample.tx_bytes
            rx_total = sample.rx_bytes
            telemetry.append(sample)
            logs.extend(events)
            ts += timedelta(seconds=interval_s)

    telemetry.sort(key=lambda item: (item.ts, item.device_id))
    logs.sort(key=lambda item: (item.ts, item.device_id))
    return telemetry, logs
