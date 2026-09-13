#!/usr/bin/env python3
"""Cross-platform automated setup script."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
VENV_DIR = ROOT_DIR / ".venv"


def log(msg):
    print(f"\n[SETUP] {msg}")


def check_prerequisites():
    log("Checking prerequisites...")
    if sys.version_info < (3, 10):
        sys.exit("Error: Python 3.10 or higher is required.")
    if not shutil.which("npm"):
        sys.exit("Error: Node.js and npm are required. Please install Node.js.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("Warning: FFmpeg/ffprobe not found on PATH. "
              "Install FFmpeg before running conversions.")


def create_virtualenv():
    if (
        sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or os.environ.get("VIRTUAL_ENV")
        or os.environ.get("CONDA_PREFIX")
    ):
        log(f"Using active Python environment at {sys.prefix}...")
        return Path(sys.executable)

    log(f"Configuring Python virtual environment at {VENV_DIR}...")
    if not VENV_DIR.exists():
        import venv
        venv.create(VENV_DIR, with_pip=True)

    if os.name == "nt":
        python_bin = VENV_DIR / "Scripts" / "python.exe"
    else:
        python_bin = VENV_DIR / "bin" / "python"
    return python_bin


def install_backend(python_bin):
    log("Installing backend dependencies...")
    # Invoke pip as a module so it can safely upgrade itself on Windows.
    pip_cmd = [str(python_bin), "-m", "pip"]
    subprocess.check_call([*pip_cmd, "install", "--upgrade", "pip"])
    req_file = BACKEND_DIR / "requirements.txt"
    if req_file.exists():
        subprocess.check_call([*pip_cmd, "install", "-r", str(req_file)])


def install_frontend():
    log("Installing frontend dependencies...")
    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    subprocess.check_call([npm_cmd, "install"], cwd=str(FRONTEND_DIR))


def setup_env():
    log("Setting up environment configuration...")
    env_example = ROOT_DIR / ".env.example"
    env_target = ROOT_DIR / ".env"
    if env_example.exists() and not env_target.exists():
        shutil.copy(env_example, env_target)
        print("Created .env from .env.example")


def main():
    check_prerequisites()
    python_bin = create_virtualenv()
    install_backend(python_bin)
    install_frontend()
    setup_env()
    log("Setup completed successfully! Use run.bat (Windows) or ./run.sh (Unix) to start.")


if __name__ == "__main__":
    main()
