"""Command-line interface.

    gateway-telemetry seed --devices 8 --hours 6 --reset
    gateway-telemetry simulate --devices 8 --interval 5
    gateway-telemetry diagnose --window-minutes 120
    gateway-telemetry serve --port 8000
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import timedelta
from pathlib import Path

from . import repository
from .config import AUTO_SEED, DEFAULT_DB_PATH, DEFAULT_WINDOW_MINUTES
from .log_parser import parse_line
from .services import run_diagnostics, seed_demo_data
from .simulator import build_devices, generate_batch
from .timeutil import utcnow


def _resolve_db(args: argparse.Namespace) -> Path:
    return Path(args.db or DEFAULT_DB_PATH)


def cmd_seed(args: argparse.Namespace) -> int:
    conn = repository.init_db(_resolve_db(args))
    try:
        counts = seed_demo_data(
            conn,
            device_count=args.devices,
            hours=args.hours,
            interval_s=args.interval,
            seed=args.seed,
            reset=args.reset,
        )
    finally:
        conn.close()

    print(
        "Seeded {devices} devices, {telemetry:,} telemetry samples, "
        "{logs:,} logs, {diagnoses} incidents.".format(**counts)
    )
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    conn = repository.init_db(_resolve_db(args))
    try:
        results = run_diagnostics(
            conn, window_minutes=args.window_minutes, device_id=args.device
        )
    finally:
        conn.close()

    incidents = [item for item in results if item.category != "healthy"]
    print(f"Classified {len(results)} devices -> {len(incidents)} incident(s).")
    for item in sorted(incidents, key=lambda d: d.score, reverse=True):
        print(
            f"  {item.device_id}: {item.root_cause} "
            f"({item.severity}, confidence {item.confidence:.0%})"
        )
        if item.evidence:
            print(f"    evidence: {', '.join(item.evidence)}")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    conn = repository.init_db(_resolve_db(args))
    devices = build_devices(args.devices)
    for device in devices:
        repository.upsert_device(
            conn,
            device.device_id,
            hardware_model=device.hardware_model,
            firmware=device.firmware,
            region=device.region,
        )
    conn.commit()

    # Warm up so the dashboard is not empty on first load.
    end = utcnow()
    start = end - timedelta(minutes=args.warmup_minutes)
    telemetry, logs = generate_batch(devices, start, end, args.interval, seed=args.seed)
    for sample in telemetry:
        repository.insert_telemetry(conn, sample.as_dict())
    for log in logs:
        repository.insert_log(conn, log.device_id, parse_line(log.raw))
    conn.commit()
    run_diagnostics(conn, window_minutes=max(60, args.warmup_minutes))
    print(
        f"Warm-up complete ({len(telemetry):,} samples). "
        "Streaming live samples; press Ctrl+C to stop."
    )

    try:
        while True:
            window_start = utcnow()
            window_end = window_start + timedelta(seconds=max(1, args.interval - 1))
            telemetry, logs = generate_batch(
                devices, window_start, window_end, args.interval, seed=args.seed
            )
            for sample in telemetry:
                repository.insert_telemetry(conn, sample.as_dict())
            for log in logs:
                repository.insert_log(conn, log.device_id, parse_line(log.raw))
            conn.commit()
            run_diagnostics(conn, window_minutes=max(60, args.warmup_minutes))
            print(
                f"{utcnow():%H:%M:%S} ingested {len(telemetry)} samples, "
                f"{len(logs)} logs"
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Stopped live simulator.")
    finally:
        conn.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    os.environ["GATEWAY_DB_PATH"] = str(_resolve_db(args))
    os.environ["GATEWAY_AUTO_SEED"] = "0" if args.no_seed else ("1" if AUTO_SEED else "0")

    print(f"Serving dashboard on http://{args.host}:{args.port} (Ctrl+C to stop)")
    uvicorn.run(
        "gateway_telemetry.api:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gateway-telemetry",
        description="Home gateway telemetry simulator, log diagnostics and dashboard.",
    )
    parser.add_argument("--db", default=None, help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="Populate a demo fleet and run diagnostics")
    seed.add_argument("--devices", type=int, default=8)
    seed.add_argument("--hours", type=int, default=6)
    seed.add_argument("--interval", type=int, default=60, help="Seconds between samples")
    seed.add_argument("--seed", type=int, default=42)
    seed.add_argument("--reset", action="store_true", help="Delete existing rows first")
    seed.set_defaults(func=cmd_seed)

    diagnose = sub.add_parser("diagnose", help="Run the root-cause classifier")
    diagnose.add_argument("--window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES)
    diagnose.add_argument("--device", default=None)
    diagnose.set_defaults(func=cmd_diagnose)

    simulate = sub.add_parser("simulate", help="Stream live simulated telemetry")
    simulate.add_argument("--devices", type=int, default=8)
    simulate.add_argument("--interval", type=int, default=5)
    simulate.add_argument("--warmup-minutes", type=int, default=60)
    simulate.add_argument("--seed", type=int, default=42)
    simulate.set_defaults(func=cmd_simulate)

    serve = sub.add_parser("serve", help="Run the API + dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--no-seed", action="store_true", help="Do not auto-seed an empty DB")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
