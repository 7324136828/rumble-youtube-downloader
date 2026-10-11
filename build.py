#!/usr/bin/env python3
"""Build portable Debug (local) and Release (LAN) Windows applications."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import venv
import uuid
from pathlib import Path

from backend.app.utils.user_settings import SettingsError, load_port_mapping

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"
OUTPUT = ROOT / "dist"
WORK = ROOT / "build"
LOCAL_VENV = ROOT / ".venv"
LOCAL_PYTHON = LOCAL_VENV / "Scripts" / "python.exe"
APPLICATION = "server"
PORT_MAPPING = ROOT / "port_mapping.json"
PROFILES = {"Debug": "127.0.0.1", "Release": "0.0.0.0"}
ARTIFACTS = ("server.exe", "public", "bin", "run.bat", "runtime.json", "port_mapping.json")

LAUNCHER = """@echo off
setlocal
if not exist "%~dp0server.exe" (
  echo [run] ERROR: Backend executable is missing. Rebuild with build.bat. 1>&2
  exit /b 1
)
if /i "%~1"=="--help" if "%~2"=="" goto launch
if /i "%~1"=="-h" if "%~2"=="" goto launch
"%~dp0server.exe" --initialize-settings
if errorlevel 1 exit /b %ERRORLEVEL%
:launch
"%~dp0server.exe" %*
exit /b %ERRORLEVEL%
"""


class BuildError(RuntimeError):
    """Raised when a portable build cannot be completed."""


def validate_port_defaults(path: Path) -> None:
    try:
        load_port_mapping(path)
    except SettingsError as error:
        raise BuildError(f"Invalid port defaults: {error}") from error


def checked_path(path: Path, parent: Path) -> Path:
    """Reject symlinks/junctions that resolve outside the intended directory."""
    resolved = path.resolve()
    allowed = parent.resolve()
    if resolved == allowed or not resolved.is_relative_to(allowed):
        raise BuildError(f"Unsafe build path outside {allowed}: {path}")
    return resolved


def run(
    command: list[str | Path], *, cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> None:
    rendered = [str(part) for part in command]
    print(f"[build] $ {subprocess.list2cmdline(rendered)}", flush=True)
    completed = subprocess.run(rendered, cwd=cwd, env=env, check=False)
    if completed.returncode:
        raise BuildError(f"Command failed with exit code {completed.returncode}")


def select_python() -> Path:
    if (
        sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or os.environ.get("VIRTUAL_ENV")
        or os.environ.get("CONDA_PREFIX")
    ):
        print(f"[build] Using active Python environment: {sys.prefix}")
        return Path(sys.executable)
    if not LOCAL_PYTHON.is_file():
        checked_path(LOCAL_VENV, ROOT)
        print(f"[build] Creating Python environment at {LOCAL_VENV}")
        venv.EnvBuilder(with_pip=True).create(LOCAL_VENV)
    if not LOCAL_PYTHON.is_file():
        raise BuildError(f"Python environment has no interpreter: {LOCAL_PYTHON}")
    return LOCAL_PYTHON


def discover_binaries(ffmpeg_dir: Path | None, js_runtime: Path | None) -> dict[str, Path]:
    """Find the external media tools and JavaScript runtime needed by yt-dlp."""
    binaries: dict[str, Path] = {}
    for name in ("ffmpeg", "ffprobe"):
        found = ffmpeg_dir / f"{name}.exe" if ffmpeg_dir else shutil.which(f"{name}.exe")
        if not found or not Path(found).is_file():
            raise BuildError(
                f"{name}.exe was not found; install FFmpeg or use --ffmpeg-dir <bin-folder>"
            )
        binaries[f"{name}.exe"] = Path(found).resolve()
    runtime = js_runtime or shutil.which("node.exe") or shutil.which("deno.exe")
    if not runtime or not Path(runtime).is_file():
        raise BuildError("Node.js or Deno is required; use --js-runtime <node.exe-or-deno.exe>")
    runtime = Path(runtime).resolve()
    if runtime.name.lower() not in ("node.exe", "deno.exe"):
        raise BuildError("--js-runtime must point to node.exe or deno.exe")
    binaries[runtime.name.lower()] = runtime
    # Shared FFmpeg builds depend on adjacent DLLs. Static builds have none.
    for parent in {path.parent for name, path in binaries.items() if name.startswith("ff")}:
        for library in parent.glob("*.dll"):
            previous = binaries.get(library.name.lower())
            if previous and previous.read_bytes() != library.read_bytes():
                raise BuildError(f"Conflicting FFmpeg DLLs: {previous} and {library}")
            binaries[library.name.lower()] = library.resolve()
    return binaries


def converter_directory(value: Path | None) -> Path | None:
    """Legacy transcription is optional and is absent from the normal checkout."""
    directory = (value or ROOT / "skill" / "original-project").resolve()
    if (directory / "download_rumble.py").is_file():
        return directory
    if value is not None:
        raise BuildError(f"No download_rumble.py found in converter directory: {directory}")
    return None


def validate_js_runtime(executable: Path, environment: dict[str, str]) -> None:
    """Reject runtimes that yt-dlp cannot use for YouTube's JS challenges."""
    name = executable.stem.lower()
    minimum = (22, 0, 0) if name == "node" else (2, 3, 0)
    try:
        completed = subprocess.run(
            [str(executable), "--version"], cwd=ROOT, env=environment,
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildError(f"Could not run bundled {name}: {error}") from error
    output = completed.stdout.strip()
    match = re.search(r"(?:^v|^deno\s+)(\d+)\.(\d+)\.(\d+)", output)
    if completed.returncode or not match:
        raise BuildError(f"Could not determine the version of bundled {name}")
    version = tuple(int(part) for part in match.groups())
    if version < minimum:
        required = ".".join(str(part) for part in minimum)
        raise BuildError(f"YouTube requires {name} {required} or newer; use --js-runtime with a supported executable")
    print(f"[build] Bundled {name}: {'.'.join(match.groups())}")


def install_dependencies(python: Path, converter: Path | None) -> None:
    probe = subprocess.run(
        [str(python), "-m", "pip", "--version"], cwd=ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if probe.returncode:
        run([python, "-m", "ensurepip", "--upgrade"])
    # Whisper serves native library transcripts. Silero VAD belongs only to the
    # optional legacy conversion script.
    requirements = BACKEND / "requirements.txt"
    if converter is None:
        requirements = WORK / "requirements-portable.txt"
        requirements.write_text("\n".join(
            line for line in (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if not line.strip().lower().startswith("silero-vad")
        ) + "\n", encoding="utf-8")
    run([python, "-m", "pip", "install", "--cache-dir", WORK / "pip-cache",
         "-r", requirements, "-r", BACKEND / "requirements-build.txt"])


def pyinstaller_command(
    python: Path, stage: Path, converter: Path | None = None,
) -> list[str | Path]:
    command: list[str | Path] = [
        python,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--noupx",
        "--name", APPLICATION,
        "--distpath", stage / "frozen",
        "--workpath", stage / "pyinstaller",
        "--specpath", stage,
        "--paths", ROOT,
        "--paths", BACKEND,
    ]
    # Uvicorn loads these configured implementations by their string names.
    for module in (
        "uvicorn.logging",
        "uvicorn.loops.asyncio",
        "uvicorn.lifespan.on",
        "uvicorn.protocols.http.h11_impl",
    ):
        command.extend(["--hidden-import", module])
    # Extractors and EJS providers use runtime module discovery, and yt-dlp
    # checks distribution metadata for its optional transports and providers.
    for package in ("yt_dlp", "yt_dlp_ejs", "curl_cffi", "whisper", "tiktoken"):
        command.extend(["--collect-all", package])
    # The maintained torch hook collects source modules/data and shared DLLs
    # without bundling its development headers or static libraries.
    command.extend(["--collect-binaries", "torch"])
    # Whisper's multilingual tokenizer discovers its encoding provider at
    # runtime. Standard NumPy/Numba/llvmlite hooks supply their native libraries.
    command.extend(["--hidden-import", "tiktoken_ext.openai_public"])
    for distribution in (
        "yt-dlp", "yt-dlp-ejs", "curl-cffi", "openai-whisper", "torch",
        "tiktoken", "numba", "llvmlite",
    ):
        command.extend(["--copy-metadata", distribution])
    if converter is None:
        for package in ("torchaudio", "silero_vad", "tensorflow", "matplotlib"):
            command.extend(["--exclude-module", package])
    else:
        command.extend(["--paths", converter, "--hidden-import", "download_rumble"])
        sources = stage / "legacy"
        sources.mkdir()
        for source in converter.rglob("*.py"):
            relative = source.relative_to(converter)
            if any(part.startswith(".") or part in ("__pycache__", "venv") for part in relative.parts):
                continue
            destination = sources / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        command.extend(["--add-data", f"{sources}{os.pathsep}legacy"])
        for package in ("silero_vad", "torchaudio"):
            command.extend(["--collect-all", package])
    command.append(BACKEND / "packaged.py")
    return command


def prepare_artifacts(stage: Path, *, defaults_path: Path = PORT_MAPPING) -> Path:
    """Create two portable packages from the shared frontend and executable."""
    validate_port_defaults(defaults_path)
    packages = stage / "packages"
    checked_path(packages, stage)
    packages.mkdir()
    for profile, host in PROFILES.items():
        package = packages / profile
        checked_path(package, packages)
        package.mkdir()
        shutil.copy2(stage / "server.exe", package / "server.exe")
        shutil.copytree(stage / "public", package / "public")
        shutil.copytree(stage / "bin", package / "bin")
        shutil.copy2(defaults_path, package / "port_mapping.json")
        (package / "run.bat").write_text(LAUNCHER, encoding="ascii", newline="\r\n")
        (package / "runtime.json").write_text(
            json.dumps({"host": host}, indent=2) + "\n", encoding="ascii",
        )
    return packages


def validate_artifacts(stage: Path, *, host: str) -> None:
    if not (stage / "server.exe").is_file():
        raise BuildError("PyInstaller did not produce the backend executable")
    if not (stage / "public" / "index.html").is_file():
        raise BuildError("Vite did not produce public/index.html")
    for executable in ("ffmpeg.exe", "ffprobe.exe"):
        if not (stage / "bin" / executable).is_file():
            raise BuildError(f"Portable media tool is missing: bin/{executable}")
    if not any((stage / "bin" / name).is_file() for name in ("node.exe", "deno.exe")):
        raise BuildError("Portable JavaScript runtime is missing from bin")
    if json.loads((stage / "runtime.json").read_text(encoding="ascii")) != {"host": host}:
        raise BuildError(f"Unexpected runtime defaults in {stage}")
    validate_port_defaults(stage / "port_mapping.json")
    batch_files = [path.relative_to(stage) for path in stage.rglob("*")
                   if path.is_file() and path.suffix.lower() == ".bat"]
    if batch_files != [Path("run.bat")]:
        raise BuildError(f"Unexpected batch files in portable artifacts: {batch_files}")


def publish_artifacts(stage: Path, *, repository: Path = ROOT) -> None:
    """Publish both profiles together, preserving their data and local settings."""
    output = repository / "dist"
    checked_path(output, repository)
    checked_path(stage, repository / "build")
    output.mkdir(exist_ok=True)
    previous = stage / "previous"
    checked_path(previous, stage)
    previous.mkdir()
    installed: list[tuple[str, str]] = []
    backed_up: list[tuple[str, str]] = []
    targets = {profile: checked_path(output / profile, output) for profile in PROFILES}
    for first, path in targets.items():
        for second, other in targets.items():
            if first != second and (path.is_relative_to(other) or other.is_relative_to(path)):
                raise BuildError(f"Portable profile folders must not overlap: {first} and {second}")
    # Check every mutation target before moving anything. In particular, a
    # junction used for one output must not leave another output half updated.
    for profile in PROFILES:
        checked_path(stage / profile, stage)
        checked_path(output / profile, output)
        checked_path(previous / profile, previous)
        for name in ARTIFACTS:
            checked_path(stage / profile / name, stage / profile)
            checked_path(output / profile / name, output / profile)
            checked_path(previous / profile / name, previous / profile)
    for profile in PROFILES:
        (output / profile).mkdir(exist_ok=True)
        (previous / profile).mkdir()
    try:
        for profile in PROFILES:
            for name in ARTIFACTS:
                source = stage / profile / name
                target = output / profile / name
                backup = previous / profile / name
                checked_path(source, stage / profile)
                checked_path(target, output / profile)
                checked_path(backup, previous / profile)
                if target.exists():
                    target.rename(backup)
                    backed_up.append((profile, name))
                source.rename(target)
                installed.append((profile, name))
    except OSError as error:
        # A locked artifact in either profile restores both previous packages.
        # User state in each distribution is never moved.
        for profile, name in reversed(installed):
            target = output / profile / name
            checked_path(target, output / profile)
            target.rename(stage / profile / name)
        for profile, name in reversed(backed_up):
            backup = previous / profile / name
            checked_path(backup, previous / profile)
            backup.rename(output / profile / name)
        raise BuildError(
            "Could not replace Debug/Release artifacts; close the running application "
            f"and retry. {error}"
        ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-install", action="store_true",
        help="Reuse installed Python/npm dependencies instead of installing them",
    )
    parser.add_argument("--ffmpeg-dir", type=Path, help="Folder containing ffmpeg.exe and ffprobe.exe")
    parser.add_argument("--js-runtime", type=Path, help="Node.js or Deno executable to include")
    parser.add_argument(
        "--converter-dir", type=Path,
        help="Optional legacy download_rumble.py folder (also bundles Silero VAD dependencies)",
    )
    options = parser.parse_args(argv)
    if os.name != "nt":
        raise BuildError("Build the Windows executable on Windows")
    if sys.version_info < (3, 10):
        raise BuildError("Python 3.10 or newer is required")
    validate_port_defaults(PORT_MAPPING)
    npm = shutil.which("npm.cmd")
    if npm is None:
        raise BuildError("Node.js and npm were not found on PATH")
    if not (BACKEND / "packaged.py").is_file():
        raise BuildError("Backend packaged entrypoint is missing")
    binaries = discover_binaries(options.ffmpeg_dir, options.js_runtime)
    converter = converter_directory(options.converter_dir)
    if converter is None:
        print("[build] Legacy conversion source absent; native library transcripts remain available")
    checked_path(WORK, ROOT)
    WORK.mkdir(exist_ok=True)
    python = select_python()
    if not options.skip_install:
        install_dependencies(python, converter)
        run([npm, "ci", "--cache", WORK / "npm-cache"], cwd=FRONTEND)

    stage = WORK / ("portable-" + uuid.uuid4().hex)
    checked_path(stage, WORK)
    stage.mkdir()
    published = False
    try:
        (stage / "bin").mkdir()
        for name, source in binaries.items():
            shutil.copy2(source, stage / "bin" / name)
        # Running with a clean PATH also verifies adjacent shared FFmpeg DLLs.
        tools_env = os.environ.copy()
        tools_env["PATH"] = str(stage / "bin") + os.pathsep + str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            run([stage / "bin" / name, "-version"], env=tools_env)
        for name in ("node.exe", "deno.exe"):
            if (stage / "bin" / name).is_file():
                validate_js_runtime(stage / "bin" / name, tools_env)
        run([npm, "run", "build", "--", "--outDir", stage / "public"], cwd=FRONTEND)
        freeze_env = os.environ.copy()
        freeze_env["PYINSTALLER_CONFIG_DIR"] = str(WORK / "pyinstaller-cache")
        checked_path(WORK / "pyinstaller-cache", WORK)
        run(pyinstaller_command(python, stage, converter), env=freeze_env)
        frozen = stage / "frozen" / f"{APPLICATION}.exe"
        checked_path(frozen, stage)
        frozen.rename(stage / "server.exe")
        # Validate both portable folders rather than PyInstaller's work/spec files.
        artifacts = prepare_artifacts(stage)
        for profile, host in PROFILES.items():
            validate_artifacts(artifacts / profile, host=host)
            run([artifacts / profile / "server.exe", "--help"])
            run([artifacts / profile / "server.exe", "--yt-dlp", "--version"])
            run([artifacts / profile / "server.exe", "--whisper-transcript", "--help"])
        publish_artifacts(artifacts)
        published = True
    finally:
        if published:
            checked_path(stage, WORK)
            shutil.rmtree(stage)
        else:
            print(f"[build] Build files retained for troubleshooting: {stage}")

    print(f"\n[build] Ready: {OUTPUT / 'Debug'} (local) and {OUTPUT / 'Release'} (LAN)")
    print("[build] Local: dist\\Debug\\run.bat")
    print("[build] LAN:   dist\\Release\\run.bat")
    print("[build] Ports: %APPDATA%\\ClipFeed\\settings.json; missing values are filled from port_mapping.json")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, OSError) as error:
        print(f"\n[build] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
