#!/usr/bin/env python3
"""Start the backend and frontend with active-environment and port discovery."""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
LOCAL_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
APP_NAME = "Rumble/YouTube Downloader Full-Stack Services"
BACKEND_RELOAD = False


def port_argument(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer between 1 and 65535") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return port


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=["serve"], default="serve")
    parser.add_argument("--lan", action="store_true",
                        help="Make the frontend available to other devices on your LAN.")
    parser.add_argument("--frontend-port", type=port_argument,
                        help="Exact frontend port (otherwise FRONTEND_PORT or 5173, auto-selected).")
    parser.add_argument("--backend-port", type=port_argument,
                        help="Exact backend port (otherwise BACKEND_PORT or 8000, auto-selected).")
    return parser.parse_args(argv)


def in_active_environment() -> bool:
    return (
        sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or bool(os.environ.get("VIRTUAL_ENV"))
        or bool(os.environ.get("CONDA_PREFIX"))
    )


def select_runtime() -> Path:
    if in_active_environment():
        return Path(sys.executable)
    if not LOCAL_PYTHON.is_file():
        print("[RUN] No active environment or local .venv; running setup.py...")
        if subprocess.call([sys.executable, str(ROOT / "setup.py")], cwd=ROOT):
            raise RuntimeError("Setup failed")
    if not LOCAL_PYTHON.is_file():
        raise RuntimeError(f"Python environment was not found at {LOCAL_PYTHON}")
    return LOCAL_PYTHON


def load_env_file() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def requested_port(variable: str, default: int) -> int:
    value = os.environ.get(variable, str(default))
    try:
        port = int(value)
    except ValueError as error:
        raise RuntimeError(f"{variable} must be an integer, not {value!r}") from error
    if not 1 <= port <= 65535:
        raise RuntimeError(f"{variable} must be between 1 and 65535")
    return port


def available_port(start: int, reserved: set[int] | None = None, *,
                   host: str = "127.0.0.1", strict: bool = False) -> int:
    reserved = reserved or set()
    for port in range(start, start + 1 if strict else 65536):
        if port in reserved:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                # Windows otherwise permits overlapping wildcard/interface binds.
                if os.name == "nt":
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind((host, port))
            except OSError:
                continue
        return port
    if strict:
        raise RuntimeError(f"TCP port {start} is unavailable on {host}; choose another port.")
    raise RuntimeError(f"No available TCP port was found at or above {start}")


def lan_addresses() -> list[str]:
    """Find candidate local IPv4 addresses without contacting an external service."""
    try:
        results = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    addresses = {ipaddress.IPv4Address(result[4][0]) for result in results}
    return [str(address) for address in sorted(addresses)
            if not (address.is_loopback or address.is_unspecified or
                    address.is_link_local or address.is_multicast)]


def merge_cors_origins(environment: dict[str, str], frontend_port: int) -> None:
    current = environment.get("CORS_ORIGINS", "")
    if current.strip() == "*":
        return
    origins = [item.strip() for item in current.split(",") if item.strip()]
    for origin in (
        f"http://localhost:{frontend_port}",
        f"http://127.0.0.1:{frontend_port}",
    ):
        if origin not in origins:
            origins.append(origin)
    environment["CORS_ORIGINS"] = ",".join(origins)


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runtime = select_runtime()
    if Path(sys.executable).resolve() != runtime.resolve():
        return subprocess.call([str(runtime), str(Path(__file__).resolve()),
                                *(sys.argv[1:] if argv is None else argv)])

    load_env_file()
    backend_host = "0.0.0.0"
    frontend_host = "0.0.0.0" if args.lan else "127.0.0.1"
    if args.backend_port is not None and args.backend_port == args.frontend_port:
        raise RuntimeError("--frontend-port and --backend-port must be different.")
    backend_port = available_port(
        args.backend_port if args.backend_port is not None else requested_port("BACKEND_PORT", 8000),
        {args.frontend_port} if args.frontend_port is not None else set(),
        host=backend_host, strict=args.backend_port is not None,
    )
    frontend_port = available_port(
        args.frontend_port if args.frontend_port is not None else requested_port("FRONTEND_PORT", 5173),
        {backend_port}, host=frontend_host, strict=args.frontend_port is not None,
    )
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://localhost:{frontend_port}"

    backend_env = os.environ.copy()
    merge_cors_origins(backend_env, frontend_port)
    backend_env["FRONTEND_URL"] = frontend_url
    backend_env["PUBLIC_BACKEND_URL"] = backend_url
    backend_env["BACKEND_HOST"] = backend_host
    backend_env["BACKEND_PORT"] = str(backend_port)

    frontend_env = os.environ.copy()
    # The proxy runs on this computer; LAN browsers keep using relative /api URLs.
    frontend_env["VITE_BACKEND_URL"] = backend_url
    frontend_env["FRONTEND_PORT"] = str(frontend_port)

    print("=" * 60)
    print(f"Starting {APP_NAME}")
    print(f"Python:   {sys.executable}")
    print(f"Backend:  {backend_url} (API docs: {backend_url}/docs)")
    print(f"Frontend: {frontend_url}")
    if args.lan:
        for address in lan_addresses() or ["<your-LAN-IP>"]:
            print(f"LAN:      http://{address}:{frontend_port}")
            print(f"LAN API:  http://{address}:{backend_port}/docs")
        print("Open a LAN URL on another device connected to the same network.")
    print("=" * 60)
    sys.stdout.flush()

    backend_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        backend_host,
        "--port",
        str(backend_port),
    ]
    if BACKEND_RELOAD:
        backend_command.append("--reload")

    npm = "npm.cmd" if os.name == "nt" else "npm"
    frontend_command = [
        npm,
        "run",
        "dev",
        "--",
        "--host",
        frontend_host,
        "--port",
        str(frontend_port),
        "--strictPort",
    ]

    backend_process: subprocess.Popen[bytes] | None = None
    frontend_process: subprocess.Popen[bytes] | None = None
    try:
        backend_process = subprocess.Popen(
            backend_command, cwd=BACKEND_DIR, env=backend_env
        )
        frontend_process = subprocess.Popen(
            frontend_command, cwd=FRONTEND_DIR, env=frontend_env
        )
        while True:
            for label, process in (
                ("Backend", backend_process),
                ("Frontend", frontend_process),
            ):
                status = process.poll()
                if status is not None:
                    print(f"[RUN] {label} exited with code {status}.")
                    return status
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[RUN] Stopping services...")
        return 0
    finally:
        stop(frontend_process)
        stop(backend_process)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"[RUN] Error: {error}", file=sys.stderr)
        raise SystemExit(1)

