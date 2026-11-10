from __future__ import annotations

from datetime import timedelta

from gateway_telemetry.log_parser import parse_line
from gateway_telemetry.simulator import build_devices, generate_batch
from gateway_telemetry.timeutil import utcnow


def test_build_devices_is_deterministic_and_profiled() -> None:
    devices = build_devices(8)

    assert [device.device_id for device in devices] == [f"GW-{i:03d}" for i in range(1, 9)]
    assert devices[0].profile == "healthy"
    assert devices[1].profile == "dns"
    assert devices[2].profile == "wan"


def test_generate_batch_is_reproducible() -> None:
    devices = build_devices(4)
    end = utcnow()
    start = end - timedelta(hours=1)

    first_telemetry, first_logs = generate_batch(devices, start, end, 300, seed=99)
    second_telemetry, second_logs = generate_batch(devices, start, end, 300, seed=99)

    assert first_telemetry == second_telemetry
    assert first_logs == second_logs


def test_generate_batch_shapes_and_ranges() -> None:
    devices = build_devices(4)
    end = utcnow()
    start = end - timedelta(hours=1)

    telemetry, logs = generate_batch(devices, start, end, 300, seed=7)

    assert len(telemetry) == 4 * 13  # 0..60 minutes inclusive at 5-minute steps
    assert {sample.device_id for sample in telemetry} == {d.device_id for d in devices}
    assert all(0 <= sample.cpu_pct <= 100 for sample in telemetry)
    assert all(0 <= sample.memory_pct <= 100 for sample in telemetry)
    assert all(0 <= sample.packet_loss_pct <= 100 for sample in telemetry)
    assert all(sample.wan_status in {"online", "offline", "degraded"} for sample in telemetry)

    assert logs, "fault profiles should emit log lines"
    parsed = [parse_line(log.raw) for log in logs]
    assert any(item.signature is not None for item in parsed)


def test_generate_batch_rejects_bad_arguments() -> None:
    devices = build_devices(1)
    end = utcnow()

    try:
        generate_batch(devices, end, end - timedelta(hours=1), 60)
    except ValueError as exc:
        assert "end must be after start" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")

    try:
        generate_batch(devices, end, end, 0)
    except ValueError as exc:
        assert "interval_s" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
