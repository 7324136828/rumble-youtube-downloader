from __future__ import annotations

import io
import json
import gzip
import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import packaged
from backend.network import discover_lan_ipv4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from backend import packaged
packaged.runtime_directory = lambda: Path(sys.argv[2])
try:
    raise SystemExit(packaged.main(sys.argv[3:]))
except (packaged.RunError, OSError) as error:
    print(f'[run] ERROR: {error}', file=sys.stderr)
    raise SystemExit(1)
"""


def free_ports() -> tuple[int, int]:
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


class PackagedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.settings_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def environment(self, root: Path | None = None) -> dict[str, str]:
        environment = os.environ.copy()
        for name in ("BACKEND_PORT", "FRONTEND_PORT", "BACKEND_HOST", "JOBS_ROOT", "JOBS_DB_PATH", "MEDIA_ROOT",
                     "CONNECTOR_PUBLIC_URL", "ORIGINAL_PROJECT_DIR", "YTDLP_JS_RUNTIME", "YTDLP_COOKIE_FILE"):
            environment.pop(name, None)
        environment["RECOMMENDATION_CONNECTOR_URL"] = "http://127.0.0.1:9"
        # Every test and running portable profile has an isolated user profile.
        environment["CLIPFEED_SETTINGS_PATH"] = str((root or self.settings_directory) / "user-settings" / "settings.json")
        return environment

    def configured_paths(self, root: Path) -> dict[str, str | bool]:
        script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from backend import packaged
packaged.load_environment(Path(sys.argv[2]))
sys.path.insert(0, str(Path(sys.argv[1]) / 'backend'))
from app import config
from app.utils import user_settings
print(json.dumps({'root': str(config.JOBS_ROOT), 'database': str(config.JOBS_DB_PATH),
                  'media': str(config.MEDIA_ROOT), 'override': config.JOBS_ROOT_ENV_OVERRIDE,
                  'settings_path': str(user_settings.settings_path())}))
"""
        completed = subprocess.run([sys.executable, "-c", script, str(PROJECT_ROOT), str(root)],
                                   env=os.environ.copy(), capture_output=True, text=True, timeout=15,
                                   check=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return json.loads(completed.stdout)

    def command(self, root: Path, *arguments: str) -> list[str]:
        return [sys.executable, "-c", ENTRYPOINT, str(PROJECT_ROOT), str(root), *arguments]

    @contextmanager
    def running(self, root: Path, frontend: int, backend: int):
        with tempfile.TemporaryFile(mode="w+b") as log:
            process = subprocess.Popen(
                self.command(root, "--frontend", str(frontend), "--backend-port", str(backend)),
                cwd=root, env=self.environment(root), stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                with httpx.Client(trust_env=False, timeout=1) as client:
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            log.seek(0)
                            self.fail(log.read().decode("utf-8", errors="replace"))
                        try:
                            response = client.get(f"http://127.0.0.1:{frontend}/api/health")
                            if response.status_code == 200:
                                break
                        except httpx.RequestError:
                            pass
                        time.sleep(0.05)
                    else:
                        self.fail("Portable frontend/API did not become ready")
                    yield client
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    def make_public(self, root: Path) -> None:
        (root / "public").mkdir()
        (root / "public" / "index.html").write_text("<h1>Portable frontend</h1>")
        (root / ".env").write_text("JOBS_ROOT=data/jobs\nJOBS_DB_PATH=data/portable.db\n")

    def test_state_paths_resolve_beside_executable_before_app_import(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, self.environment(), clear=True):
            root = Path(directory)
            (root / ".env").write_text("JOBS_ROOT=state/jobs\nJOBS_DB_PATH=state/history.db\nMEDIA_ROOT=media\nYTDLP_COOKIE_FILE=cookies.txt\n")
            packaged.load_environment(root)
            for key, relative in (("JOBS_ROOT", "state/jobs"), ("JOBS_DB_PATH", "state/history.db"),
                                  ("MEDIA_ROOT", "media"), ("YTDLP_COOKIE_FILE", "cookies.txt")):
                self.assertEqual(Path(os.environ[key]), (root / relative).resolve())

    def test_default_storage_uses_system_temp_and_tools_follow_portable_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, self.environment(), clear=True):
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "bin" / "node.exe").touch()
            packaged.load_environment(root)
            jobs = (Path(tempfile.gettempdir()) / "prod_jobs").resolve()
            for key in ("JOBS_ROOT", "JOBS_DB_PATH", "MEDIA_ROOT"):
                self.assertNotIn(key, os.environ)
            paths = self.configured_paths(root)
            self.assertEqual(Path(paths["root"]), jobs)
            self.assertEqual(Path(paths["database"]), jobs / "jobs.db")
            self.assertEqual(Path(paths["media"]), jobs / "library")
            self.assertFalse(paths["override"])
            self.assertFalse(Path(paths["settings_path"]).exists())
            self.assertTrue(os.environ["PATH"].startswith(str(root / "bin") + os.pathsep))
            self.assertEqual(os.environ["YTDLP_JS_RUNTIME"], f"node:{root / 'bin' / 'node.exe'}")
            self.assertEqual(Path(os.environ["ORIGINAL_PROJECT_DIR"]), root / "skill" / "original-project")

    def test_empty_storage_values_use_temp_defaults_in_each_portable_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory)
            jobs = (Path(tempfile.gettempdir()) / "prod_jobs").resolve()
            for profile in ("Debug", "Release"):
                with self.subTest(profile=profile):
                    root = location / profile
                    root.mkdir()
                    (root / ".env").write_text("JOBS_ROOT=   \nJOBS_DB_PATH=   \nMEDIA_ROOT=   \n")
                    with (
                        patch.dict(os.environ, self.environment(), clear=True),
                        patch.object(packaged.sys, "_MEIPASS", str(location / "extracted"), create=True),
                    ):
                        packaged.load_environment(root)
                        paths = self.configured_paths(root)
                        self.assertEqual(Path(paths["root"]), jobs)
                        self.assertEqual(Path(paths["database"]), jobs / "jobs.db")
                        self.assertEqual(Path(paths["media"]), jobs / "library")
                        self.assertFalse(paths["override"])

    def test_process_storage_overrides_take_precedence_and_keep_absolute_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory)
            root = location / "portable"
            root.mkdir()
            (root / ".env").write_text("JOBS_ROOT=ignored\nJOBS_DB_PATH=ignored.db\nMEDIA_ROOT=ignored-media\n")
            overrides = {
                "JOBS_ROOT": str(location / "jobs"),
                "JOBS_DB_PATH": str(location / "metadata" / "history.db"),
                "MEDIA_ROOT": str(location / "videos"),
            }
            with patch.dict(os.environ, {**self.environment(), **overrides}, clear=True):
                packaged.load_environment(root)
                for key, expected in overrides.items():
                    self.assertEqual(Path(os.environ[key]), Path(expected).resolve())

    def test_custom_jobs_root_supplies_default_database_and_media_locations(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, self.environment(), clear=True):
            root = Path(directory)
            (root / ".env").write_text("JOBS_ROOT=state/jobs\n")
            packaged.load_environment(root)
            jobs = (root / "state" / "jobs").resolve()
            self.assertEqual(Path(os.environ["JOBS_ROOT"]), jobs)
            self.assertNotIn("JOBS_DB_PATH", os.environ)
            self.assertNotIn("MEDIA_ROOT", os.environ)
            paths = self.configured_paths(root)
            self.assertEqual(Path(paths["database"]), jobs / "jobs.db")
            self.assertEqual(Path(paths["media"]), jobs / "library")
            self.assertTrue(paths["override"])

    def test_saved_storage_is_shared_between_portable_profiles_and_explicit_overrides_win(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, self.environment(), clear=True):
            location = Path(directory)
            selected = location / "selected-storage"
            settings = Path(os.environ["CLIPFEED_SETTINGS_PATH"])
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({"data_root": str(selected)}), encoding="utf-8")
            for profile in ("Debug", "Release"):
                with self.subTest(profile=profile):
                    root = location / profile
                    root.mkdir()
                    paths = self.configured_paths(root)
                    self.assertEqual(Path(paths["root"]), selected)
                    self.assertEqual(Path(paths["database"]), selected / "jobs.db")
                    self.assertEqual(Path(paths["media"]), selected / "library")
                    self.assertFalse(paths["override"])
                    (root / ".env").write_text("JOBS_ROOT=state/jobs\n", encoding="utf-8")
                    paths = self.configured_paths(root)
                    self.assertEqual(Path(paths["root"]), root / "state" / "jobs")
                    self.assertTrue(paths["override"])

    def test_empty_converter_override_uses_stable_fallback_or_bundled_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extracted = root / "unpacked"
            (extracted / "legacy").mkdir(parents=True)
            (root / ".env").write_text("ORIGINAL_PROJECT_DIR=\n", encoding="utf-8")
            for bundled in (False, True):
                with self.subTest(bundled=bundled):
                    if bundled:
                        (extracted / "legacy" / "download_rumble.py").touch()
                    with patch.dict(os.environ, self.environment(), clear=True), patch.object(packaged.sys, "_MEIPASS", str(extracted), create=True):
                        packaged.load_environment(root)
                        expected = extracted / "legacy" if bundled else root / "skill" / "original-project"
                        self.assertEqual(Path(os.environ["ORIGINAL_PROJECT_DIR"]), expected.resolve())

    def test_extractor_dispatch_does_not_load_server_environment_or_bind_ports(self):
        with patch("yt_dlp.main", return_value=0) as extractor, patch.object(packaged, "load_environment") as environment:
            self.assertEqual(packaged.main(["--yt-dlp", "--version"]), 0)
            extractor.assert_called_once_with(["--version"])
            environment.assert_not_called()

    def test_whisper_dispatch_preserves_arguments_exit_status_and_skips_server_startup(self):
        with (
            patch.object(sys, "path", [str(PROJECT_ROOT / "backend"), *sys.path]),
            patch("app.utils.transcribe.run", return_value=7) as transcribe,
            patch.object(packaged, "load_environment") as environment,
            patch.object(packaged, "bind_listener") as bind,
        ):
            arguments = ["--input", "video.mp4", "--output", "transcript.txt", "--model", "base"]
            self.assertEqual(packaged.main(["--whisper-transcript", *arguments]), 7)
            transcribe.assert_called_once_with(arguments)
            environment.assert_not_called()
            bind.assert_not_called()

    def test_default_lan_and_explicit_bind_configuration(self) -> None:
        with patch.dict(os.environ, self.environment(), clear=True):
            self.assertEqual(packaged.parse_args([]).host, "0.0.0.0")
            self.assertEqual(packaged.parse_args([], default_host="127.0.0.1").host, "127.0.0.1")
            self.assertEqual(packaged.parse_args(["--host", "127.0.0.1"]).host, "127.0.0.1")
            with patch.dict(os.environ, {"BACKEND_HOST": "192.168.1.156"}):
                self.assertEqual(packaged.parse_args([]).host, "192.168.1.156")
                self.assertEqual(
                    packaged.parse_args([], default_host="127.0.0.1").host, "192.168.1.156",
                )
                self.assertEqual(packaged.parse_args(["--host", "127.0.0.1"]).host, "127.0.0.1")

    def test_runtime_profile_defaults_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "runtime.json"
            self.assertEqual(packaged.runtime_host(root), "0.0.0.0")
            for host in ("127.0.0.1", "0.0.0.0"):
                profile.write_text(json.dumps({"host": host}), encoding="utf-8")
                self.assertEqual(packaged.runtime_host(root), host)
            for content in (
                "{", "[]", "{}", '{"host": "localhost"}',
                '{"host": null}', '{"host": ["127.0.0.1"]}',
            ):
                with self.subTest(content=content):
                    profile.write_text(content, encoding="utf-8")
                    with self.assertRaises(packaged.RunError) as raised:
                        packaged.runtime_host(root)
                    self.assertIn(str(profile), str(raised.exception))
                    self.assertIn("runtime profile", str(raised.exception))
            profile.unlink()
            profile.mkdir()
            with self.assertRaisesRegex(packaged.RunError, "Cannot read runtime profile"):
                packaged.runtime_host(root)

    def test_frozen_runtime_profile_follows_executable_after_folder_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Debug"
            root.mkdir()
            (root / "runtime.json").write_text('{"host": "127.0.0.1"}', encoding="utf-8")
            for name in ("Debug", "RenamedPortable"):
                with self.subTest(folder=name):
                    if root.name != name:
                        root = root.rename(root.with_name(name))
                    with (
                        patch.object(packaged.sys, "frozen", True, create=True),
                        patch.object(packaged.sys, "executable", str(root / "server.exe")),
                    ):
                        self.assertEqual(packaged.runtime_directory(), root.resolve())
                        self.assertEqual(packaged.runtime_host(packaged.runtime_directory()), "127.0.0.1")

    def test_profile_environment_file_process_environment_and_cli_precedence(self) -> None:
        for profile_host in ("127.0.0.1", "0.0.0.0"):
            for file_host, process_host, arguments, expected in (
                (None, None, [], profile_host),
                ("192.168.1.156", None, [], "192.168.1.156"),
                ("192.168.1.156", "192.168.1.157", [], "192.168.1.157"),
                ("192.168.1.156", "192.168.1.157", ["--host", "127.0.0.1"], "127.0.0.1"),
            ):
                with self.subTest(profile=profile_host, file=file_host, process=process_host, cli=arguments):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        self.make_public(root)
                        (root / "runtime.json").write_text(json.dumps({"host": profile_host}), encoding="utf-8")
                        if file_host is not None:
                            (root / ".env").write_text(f"BACKEND_HOST={file_host}\n", encoding="utf-8")
                        environment = self.environment()
                        if process_host is not None:
                            environment["BACKEND_HOST"] = process_host
                        with (
                            patch.dict(os.environ, environment, clear=True),
                            patch.object(packaged, "runtime_directory", return_value=root),
                            patch.object(packaged, "bind_listener", side_effect=packaged.RunError("binding checked")) as bind,
                            self.assertRaisesRegex(packaged.RunError, "binding checked"),
                        ):
                            packaged.main(arguments)
                        bind.assert_called_once_with(expected, 8000, "backend")

    def test_startup_links_follow_binding_and_proxy_uses_local_backend(self) -> None:
        addresses = ["192.168.1.156", "192.168.56.1"]
        for host, arguments, expected_addresses in (
            ("0.0.0.0", [], addresses),
            ("127.0.0.1", ["--host", "127.0.0.1"], []),
            ("192.168.1.156", ["--host", "192.168.1.156"], addresses[:1]),
        ):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.make_public(root)
                backend_socket = Mock(spec=socket.socket, family=socket.AF_INET)
                frontend_socket = Mock(spec=socket.socket, family=socket.AF_INET)
                backend_socket.getsockname.return_value = (host, 8000)
                frontend_socket.getsockname.return_value = (host, 3000)
                output = io.StringIO()
                with (
                    patch.dict(os.environ, self.environment(), clear=True),
                    patch.object(packaged, "runtime_directory", return_value=root),
                    patch.object(packaged, "bind_listener", side_effect=[backend_socket, frontend_socket]) as bind,
                    patch("backend.network.discover_lan_ipv4", return_value=addresses),
                    patch.object(packaged, "create_frontend_app") as frontend_app,
                    patch.object(packaged, "serve_applications", new=Mock(return_value=object())),
                    patch.object(packaged.asyncio, "run", return_value=0),
                    redirect_stdout(output),
                ):
                    self.assertEqual(packaged.main(arguments), 0)
                self.assertEqual(bind.call_args_list[0].args, (host, 8000, "backend"))
                self.assertEqual(bind.call_args_list[1].args, (host, 3000, "frontend"))
                local_host = "127.0.0.1" if host == "0.0.0.0" else host
                frontend_app.assert_called_once_with(root / "public", f"http://{local_host}:8000")
                lines = output.getvalue().splitlines()
                self.assertIn("Starting Rumble/YouTube Conversion Services", lines)
                self.assertIn(f"Frontend: http://{local_host}:3000", lines)
                self.assertIn(
                    f"Backend:  http://{local_host}:8000 (API docs: http://{local_host}:8000/docs)",
                    lines,
                )
                self.assertEqual(
                    [line for line in lines if line.startswith("LAN: ")],
                    [f"LAN:      http://{address}:3000" for address in expected_addresses],
                )
                self.assertEqual(
                    [line for line in lines if line.startswith("LAN API: ")],
                    [f"LAN API:  http://{address}:8000/docs" for address in expected_addresses],
                )
                backend_socket.close.assert_called_once()
                frontend_socket.close.assert_called_once()

    def test_release_profile_serves_frontend_and_proxy_over_lan(self) -> None:
        addresses = discover_lan_ipv4()
        if not addresses:
            self.skipTest("No local LAN IPv4 address is available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Release"
            root.mkdir()
            self.make_public(root)
            (root / "runtime.json").write_text('{"host": "0.0.0.0"}', encoding="utf-8")
            frontend, backend = free_ports()
            with self.running(root, frontend, backend) as client:
                for address in addresses:
                    with self.subTest(address=address):
                        frontend_url = f"http://{address}:{frontend}"
                        backend_url = f"http://{address}:{backend}"
                        self.assertIn("Portable frontend", client.get(frontend_url).text)
                        self.assertEqual(client.get(frontend_url + "/api/health").json(), {"status": "ok"})
                        self.assertEqual(client.get(backend_url + "/docs").status_code, 200)
                        settings = client.patch(frontend_url + "/api/settings/downloads", json={"generate_thumbnails": False})
                        self.assertEqual(settings.status_code, 200)
                        self.assertFalse(client.get(backend_url + "/api/settings/downloads").json()["generate_thumbnails"])

    def test_debug_profile_serves_local_proxy_and_refuses_lan_after_folder_rename(self) -> None:
        addresses = discover_lan_ipv4()
        if not addresses:
            self.skipTest("No local LAN IPv4 address is available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Debug"
            root.mkdir()
            self.make_public(root)
            (root / "runtime.json").write_text('{"host": "127.0.0.1"}', encoding="utf-8")
            root = root.rename(root.with_name("RenamedPortable"))
            frontend, backend = free_ports()
            frontend_url = f"http://127.0.0.1:{frontend}"
            backend_url = f"http://127.0.0.1:{backend}"
            with self.running(root, frontend, backend) as client:
                self.assertIn("Portable frontend", client.get(frontend_url).text)
                self.assertEqual(client.get(frontend_url + "/api/health").json(), {"status": "ok"})
                self.assertEqual(client.patch(frontend_url + "/api/settings/downloads", json={"generate_thumbnails": False}).status_code, 200)
                self.assertFalse(client.get(backend_url + "/api/settings/downloads").json()["generate_thumbnails"])
                for address in addresses:
                    for port in (frontend, backend):
                        with self.subTest(address=address, port=port):
                            with self.assertRaises((httpx.ConnectError, httpx.ConnectTimeout)):
                                client.get(f"http://{address}:{port}/api/health")

    def test_separate_ports_proxy_static_files_and_persistent_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_public(root)
            frontend, backend = free_ports()
            frontend_url = f"http://127.0.0.1:{frontend}"
            backend_url = f"http://127.0.0.1:{backend}"
            with self.running(root, frontend, backend) as client:
                self.assertIn("Portable frontend", client.get(frontend_url).text)
                self.assertEqual(client.get(backend_url + "/api/health").json(), {"status": "ok"})
                self.assertTrue(client.get(frontend_url + "/api/settings/downloads").json()["generate_thumbnails"])
                self.assertEqual(client.patch(frontend_url + "/api/settings/downloads", json={"generate_thumbnails": False}).status_code, 200)
                self.assertFalse(client.get(backend_url + "/api/settings/downloads").json()["generate_thumbnails"])
                self.assertEqual(client.get(frontend_url + "/%2e%2e/.env").status_code, 404)
            self.assertTrue((root / "data" / "portable.db").is_file())
            settings_file = root / "user-settings" / "settings.json"
            self.assertEqual(json.loads(settings_file.read_text(encoding="utf-8")), {"data_root": None})
            frontend, backend = free_ports()
            with self.running(root, frontend, backend) as client:
                frontend_url = f"http://127.0.0.1:{frontend}"
                self.assertFalse(client.get(frontend_url + "/api/settings/downloads").json()["generate_thumbnails"])
                self.assertTrue(client.patch(frontend_url + "/api/settings/downloads", json={"generate_thumbnails": True}).json()["generate_thumbnails"])

    def test_saved_storage_applies_only_after_actual_server_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_public(root)
            (root / ".env").write_text("", encoding="utf-8")
            initial = root / "original-library"
            selected = root / "new-library"
            settings_file = root / "user-settings" / "settings.json"
            settings_file.parent.mkdir()
            settings_file.write_text(json.dumps({"data_root": str(initial), "unrelated": "preserved"}), encoding="utf-8")
            frontend, backend = free_ports()
            with self.running(root, frontend, backend) as client:
                url = f"http://127.0.0.1:{frontend}"
                before = client.get(url + "/api/settings/storage").json()
                self.assertEqual(Path(before["active_data_root"]), initial)
                self.assertFalse(before["environment_override"])
                self.assertFalse(before["restart_required"])
                self.assertEqual(client.patch(url + "/api/settings/downloads", json={"generate_thumbnails": False}).status_code, 200)
                saved = client.patch(url + "/api/settings/storage", json={"data_root": str(selected)})
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(Path(saved.json()["active_data_root"]), initial)
                self.assertEqual(Path(saved.json()["effective_data_root"]), selected)
                self.assertTrue(saved.json()["restart_required"])
                self.assertFalse(client.get(url + "/api/settings/downloads").json()["generate_thumbnails"])
                self.assertFalse((selected / "jobs.db").exists())
            self.assertTrue((initial / "jobs.db").is_file())
            self.assertEqual(json.loads(settings_file.read_text(encoding="utf-8")),
                             {"data_root": str(selected.resolve()), "unrelated": "preserved"})
            frontend, backend = free_ports()
            with self.running(root, frontend, backend) as client:
                url = f"http://127.0.0.1:{frontend}"
                after = client.get(url + "/api/settings/storage").json()
                self.assertEqual(Path(after["active_data_root"]), selected)
                self.assertEqual(Path(after["active_database_path"]), selected / "jobs.db")
                self.assertEqual(Path(after["active_media_root"]), selected / "library")
                self.assertFalse(after["restart_required"])
                self.assertTrue(client.get(url + "/api/settings/downloads").json()["generate_thumbnails"])
            self.assertTrue((selected / "jobs.db").is_file())

    def test_busy_frontend_port_fails_and_releases_backend_port(self) -> None:
        with tempfile.TemporaryDirectory() as directory, socket.socket() as busy:
            root = Path(directory)
            self.make_public(root)
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            frontend = busy.getsockname()[1]
            _, backend = free_ports()
            completed = subprocess.run(
                self.command(root, "--frontend-port", str(frontend), "--backend", str(backend)),
                cwd=root, env=self.environment(), capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("Cannot bind frontend", completed.stderr)
            with socket.socket() as released:
                released.bind(("127.0.0.1", backend))

    def test_invalid_and_duplicate_ports_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for arguments in (("--frontend", "0"), ("--backend", "65536"),
                              ("--backend", "abc"), ("--frontend", "8000")):
                with self.subTest(arguments=arguments):
                    completed = subprocess.run(
                        self.command(Path(directory), *arguments), env=self.environment(),
                        capture_output=True, text=True, timeout=15,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn("error:", completed.stderr)

    def test_backend_startup_failure_stops_both_services(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_public(root)
            (root / "blocked").write_text("A file cannot hold a database directory.")
            (root / ".env").write_text("JOBS_ROOT=data/jobs\nJOBS_DB_PATH=blocked/jobs.db\n")
            frontend, backend = free_ports()
            completed = subprocess.run(
                self.command(root, "--frontend", str(frontend), "--backend", str(backend)),
                cwd=root, env=self.environment(), capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("Backend failed", completed.stderr)
            for port in (frontend, backend):
                with socket.socket() as released:
                    released.bind(("127.0.0.1", port))


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.started = False

    async def __aiter__(self):
        self.started = True
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        await asyncio.sleep(0)
        self.closed = True


class ProxyStreamingTests(unittest.TestCase):
    def client(self, root, handler):
        (root / "index.html").write_text("frontend", encoding="utf-8")
        original = httpx.AsyncClient

        def with_transport(*args, **kwargs):
            return original(*args, transport=httpx.MockTransport(handler), **kwargs)

        self.enterContext(patch.object(packaged.httpx, "AsyncClient", with_transport))
        return self.enterContext(TestClient(packaged.create_frontend_app(root, "http://127.0.0.1:8000")))

    def test_ranges_raw_encoding_and_multiple_chunks_survive_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = gzip.compress(b"streamed media" * 10000)
            stream = TrackedStream([raw[:20], raw[20:]])

            async def handler(request):
                self.assertEqual(request.headers["range"], "bytes=0-99")
                self.assertNotIn("x-drop", request.headers)
                self.assertEqual(request.url.raw_path, b"/api/media/a%2Fb/stream?format=original")
                return httpx.Response(206, stream=stream, headers={
                    "content-encoding": "gzip", "content-length": str(len(raw)),
                    "content-range": "bytes 0-99/1000", "accept-ranges": "bytes",
                    "content-type": "video/mp4", "connection": "x-drop", "x-drop": "remove",
                })

            client = self.client(Path(directory), handler)
            response = client.get("/api/media/a%2Fb/stream?format=original", headers={
                "range": "bytes=0-99", "connection": "x-drop", "x-drop": "remove",
            })
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, gzip.decompress(raw))
            self.assertEqual(response.headers["content-length"], str(len(raw)))
            self.assertEqual(response.headers["content-range"], "bytes 0-99/1000")
            self.assertEqual(response.headers["accept-ranges"], "bytes")
            self.assertNotIn("x-drop", response.headers)
            self.assertTrue(stream.closed)

    def test_multipart_binary_upload_keeps_boundary_fields_and_content(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = bytes(range(256)) * 4096
            seen = []

            async def handler(request):
                body = await request.aread()
                seen.append(body)
                self.assertIn(payload, body)
                self.assertIn(b'name="title"', body)
                self.assertIn(b"LAN upload", body)
                self.assertIn(b'filename="recording.webm"', body)
                self.assertIn("multipart/form-data; boundary=", request.headers["content-type"])
                self.assertEqual(int(request.headers["content-length"]), len(body))
                return httpx.Response(201, stream=TrackedStream([b'{"saved":true}']), headers={"content-type": "application/json"})

            client = self.client(Path(directory), handler)
            response = client.post("/api/media/upload", data={"title": "LAN upload"},
                                   files={"file": ("recording.webm", payload, "video/webm")})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json(), {"saved": True})
            self.assertEqual(len(seen), 1)

    def test_disconnect_before_body_and_during_body_always_closes_upstream(self):
        for failed_message in ("http.response.start", "http.response.body"):
            with self.subTest(failed_message=failed_message):
                stream = TrackedStream([b"first", b"second"])
                response = packaged.ProxyStreamingResponse(httpx.Response(200, stream=stream))

                async def send(message):
                    if message["type"] == failed_message:
                        raise OSError("client disconnected")

                async def receive():
                    return {"type": "http.disconnect"}

                async def execute():
                    with self.assertRaises(ClientDisconnect):
                        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

                asyncio.run(execute())
                self.assertTrue(stream.closed)

    def test_cancellation_disconnect_shields_upstream_close(self):
        stream = TrackedStream([b"first", b"second"])
        response = packaged.ProxyStreamingResponse(httpx.Response(200, stream=stream))

        async def send(message):
            if message["type"] == "http.response.body":
                await asyncio.sleep(10)

        async def receive():
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}

        asyncio.run(response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send))
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
