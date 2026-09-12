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
    log(f"Configuring Python virtual environment at {VENV_DIR}...")
    if not VENV_DIR.exists():
        import venv
        venv.create(VENV_DIR, with_pip=True)

    if os.name == "nt":
        pip_bin = VENV_DIR / "Scripts" / "pip.exe"
    else:
        pip_bin = VENV_DIR / "bin" / "pip"
    return pip_bin


def install_backend(pip_bin):
    log("Installing backend dependencies...")
    subprocess.check_call([str(pip_bin), "install", "--upgrade", "pip"])
    req_file = BACKEND_DIR / "requirements.txt"
    if req_file.exists():
        subprocess.check_call([str(pip_bin), "install", "-r", str(req_file)])


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
    pip_bin = create_virtualenv()
    install_backend(pip_bin)
    install_frontend()
    setup_env()
    log("Setup completed successfully! Use run.bat (Windows) or ./run.sh (Unix) to start.")


if __name__ == "__main__":
    main()
