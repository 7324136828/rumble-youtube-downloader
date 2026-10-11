"""User-level settings kept separately from the selected data directory."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

APP_NAME = "ClipFeed"
_LOCK = threading.RLock()
_MAX_SETTINGS_BYTES = 1024 * 1024


class SettingsError(RuntimeError):
    """The user's settings file could not be read or safely updated."""


def settings_path() -> Path:
    override = os.environ.get("CLIPFEED_SETTINGS_PATH", "").strip()
    if override:
        path = Path(os.path.expandvars(override)).expanduser()
        if not path.is_absolute():
            raise SettingsError("CLIPFEED_SETTINGS_PATH must be an absolute settings.json path.")
        return path.resolve()
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root.expanduser().resolve() / APP_NAME / "settings.json"


def default_data_root() -> Path:
    return (Path(tempfile.gettempdir()) / "prod_jobs").resolve()


def normalize_data_root(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Storage folder must be text no longer than 2048 characters.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Storage folder cannot contain control characters.")
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1].strip()
    if not value:
        return None
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        raise ValueError("Enter an absolute storage folder on the computer running ClipFeed.")
    return str(path.resolve())


def _read_json_object(path: Path) -> dict:
    try:
        with path.open("rb") as source:
            raw = source.read(_MAX_SETTINGS_BYTES + 1)
        if len(raw) > _MAX_SETTINGS_BYTES:
            raise SettingsError(f"Settings file is too large: {path}")
        data = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(data, dict):
            raise SettingsError(f"Settings file must contain a JSON object: {path}")
        return data
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SettingsError(f"Cannot read settings at {path}: {error}") from error


def _read(path: Path) -> dict:
    if not path.exists():
        return {"data_root": None}
    data = _read_json_object(path)
    try:
        data["data_root"] = normalize_data_root(data.get("data_root"))
    except ValueError as error:
        raise SettingsError(f"Cannot read settings at {path}: {error}") from error
    return data


def _write(path: Path, data: dict, *, overwrite: bool = True) -> None:
    staged = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Stage on the same filesystem so replacing the existing JSON is atomic.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".settings-", suffix=".tmp", delete=False) as output:
            staged = Path(output.name)
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            os.replace(staged, path)
        else:
            try:
                if os.name == "nt":
                    # Windows rename does not replace a file another startup created.
                    staged.rename(path)
                else:
                    os.link(staged, path)
            except FileExistsError:
                pass
    except OSError as error:
        raise SettingsError(f"Cannot save settings at {path}: {error}") from error
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def validate_port_settings(values: dict, path: Path) -> dict[str, int]:
    """Require two distinct integer ports without coercing user preferences."""
    ports = {}
    for field in ("frontend_port", "backend_port"):
        if field not in values:
            raise SettingsError(f"Invalid configuration at {path}: missing {field}")
        value = values[field]
        if type(value) is not int or not 1 <= value <= 65535:
            raise SettingsError(
                f"Invalid configuration at {path}: {field} must be an integer from 1 to 65535"
            )
        ports[field] = value
    if ports["frontend_port"] == ports["backend_port"]:
        raise SettingsError(
            f"Invalid configuration at {path}: frontend_port and backend_port must be different"
        )
    return ports


def load_port_mapping(path: Path) -> dict[str, int]:
    """Read the frontend and backend defaults shipped with the application."""
    return validate_port_settings(_read_json_object(path), path)


def initialize_port_settings(defaults_path: Path) -> dict[str, int]:
    """Fill absent ports, preserve other preferences, and reread the saved JSON."""
    defaults = load_port_mapping(defaults_path)
    with _LOCK:
        path = settings_path()
        if not path.exists():
            _write(path, {}, overwrite=False)
        values = _read_json_object(path)
        missing = [field for field in defaults if field not in values]
        for field in missing:
            values[field] = defaults[field]
        # An invalid existing port must fail before modifying any preferences.
        validate_port_settings(values, path)
        if missing:
            _write(path, values)
        return validate_port_settings(_read_json_object(path), path)


def read_settings(*, create: bool = False) -> dict:
    with _LOCK:
        path = settings_path()
        data = _read(path)
        if create and not path.exists():
            _write(path, data)
        return data


def ensure_settings_file() -> None:
    read_settings(create=True)


def resolve_data_root() -> Path:
    override = os.environ.get("JOBS_ROOT", "").strip()
    if override:
        return Path(os.path.expandvars(override)).expanduser().resolve()
    saved = read_settings().get("data_root")
    return Path(saved) if saved else default_data_root()


def storage_payload() -> dict:
    # config captures the running paths at startup. Reading the JSON afterward
    # describes the next startup without switching a live SQLite database.
    from .. import config

    data = read_settings()
    override = config.JOBS_ROOT_ENV_OVERRIDE
    effective = config.JOBS_ROOT if override else Path(data["data_root"] or default_data_root())
    return {"data_root": data["data_root"], "default_data_root": str(default_data_root()),
            "active_data_root": str(config.JOBS_ROOT), "effective_data_root": str(effective),
            "settings_path": str(settings_path()), "restart_required": effective != config.JOBS_ROOT,
            "environment_override": override, "active_database_path": str(config.JOBS_DB_PATH),
            "active_media_root": str(config.MEDIA_ROOT)}


def update_storage(value: str | None) -> dict:
    normalized = normalize_data_root(value)
    destination = Path(normalized) if normalized else default_data_root()
    # Catch a file path or unwritable destination before saving a preference
    # that would prevent the app from starting after its next restart.
    if destination.exists() and not destination.is_dir():
        raise ValueError("Storage folder must be a directory.")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=destination) as probe:
        probe.write(b"ClipFeed storage check")
        probe.flush()
    with _LOCK:
        path = settings_path()
        data = _read(path)
        data["data_root"] = normalized
        _write(path, data)
    return storage_payload()
