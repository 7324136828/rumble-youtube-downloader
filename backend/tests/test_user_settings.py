"""Persistent storage preferences, isolated from the user's settings and library."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils import user_settings


class UserSettingsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.settings_file = self.folder / "config" / "settings.json"
        self.port_mapping = self.folder / "port_mapping.json"
        self.port_mapping.write_text(json.dumps({"frontend_port": 4123, "backend_port": 8010}),
                                     encoding="utf-8")
        self.default_root = self.folder / "system-temp" / "prod_jobs"
        environment = os.environ.copy()
        for key in ("JOBS_ROOT", "JOBS_DB_PATH", "MEDIA_ROOT"):
            environment.pop(key, None)
        environment.update({"CLIPFEED_SETTINGS_PATH": str(self.settings_file),
                            "APPDATA": str(self.folder / "appdata")})
        self.enterContext(patch.dict(os.environ, environment, clear=True))
        self.enterContext(patch.object(user_settings.tempfile, "gettempdir",
                                      return_value=str(self.default_root.parent)))

        # The running paths stay fixed until restart even when settings change.
        from app import config
        self.config = config
        for key, value in (("JOBS_ROOT", self.default_root),
                           ("JOBS_DB_PATH", self.default_root / "jobs.db"),
                           ("MEDIA_ROOT", self.default_root / "library"),
                           ("JOBS_ROOT_ENV_OVERRIDE", False)):
            self.enterContext(patch.object(config, key, value))

    def write_settings(self, data):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps(data), encoding="utf-8")

    def test_reads_and_default_resolution_do_not_create_user_state(self):
        self.assertEqual(user_settings.settings_path(), self.settings_file)
        self.assertEqual(user_settings.read_settings(), {"data_root": None})
        self.assertEqual(user_settings.resolve_data_root(), self.default_root)
        self.assertFalse(self.settings_file.parent.exists())
        self.assertFalse(self.default_root.exists())

    def test_platform_settings_locations_are_separate_from_storage(self):
        home = self.folder / "home"
        for name, platform, environment, expected in (
            ("nt", "win32", {"APPDATA": str(self.folder / "roaming")},
             self.folder / "roaming" / "ClipFeed" / "settings.json"),
            ("nt", "win32", {}, home / "AppData" / "Roaming" / "ClipFeed" / "settings.json"),
            ("posix", "darwin", {}, home / "Library" / "Application Support" / "ClipFeed" / "settings.json"),
            ("posix", "linux", {"XDG_CONFIG_HOME": str(self.folder / "xdg")},
             self.folder / "xdg" / "ClipFeed" / "settings.json"),
            ("posix", "linux", {}, home / ".config" / "ClipFeed" / "settings.json"),
        ):
            with self.subTest(platform=platform, environment=environment), \
                 patch.object(user_settings, "os", SimpleNamespace(name=name, environ=environment)), \
                 patch.object(user_settings.sys, "platform", platform), \
                 patch.object(Path, "home", return_value=home):
                self.assertEqual(user_settings.settings_path(), expected)
        self.assertFalse(home.exists())

    def test_settings_file_override_must_be_absolute(self):
        with patch.dict(os.environ, {"CLIPFEED_SETTINGS_PATH": "relative/settings.json"}):
            self.assertRaises(user_settings.SettingsError, user_settings.settings_path)

    def test_ensure_creates_default_json_once(self):
        user_settings.ensure_settings_file()
        self.assertEqual(json.loads(self.settings_file.read_text(encoding="utf-8")),
                         {"data_root": None})
        self.write_settings({"data_root": str(self.folder / "saved"),
                             "future_option": {"enabled": True}})
        before = self.settings_file.read_bytes()
        user_settings.ensure_settings_file()
        self.assertEqual(self.settings_file.read_bytes(), before)

    def test_port_initialization_creates_empty_json_then_saves_project_defaults(self):
        with patch.object(user_settings, "_write", wraps=user_settings._write) as write:
            ports = user_settings.initialize_port_settings(self.port_mapping)
        self.assertEqual(ports, {"frontend_port": 4123, "backend_port": 8010})
        self.assertEqual(write.call_args_list[0].args, (self.settings_file, {}))
        self.assertEqual(write.call_args_list[0].kwargs, {"overwrite": False})
        self.assertEqual(json.loads(self.settings_file.read_text(encoding="utf-8")), ports)
        self.assertFalse(self.default_root.exists())

    def test_port_initialization_fills_only_absent_keys_and_preserves_other_preferences(self):
        # Preserve the raw storage preference, including its spelling and spacing.
        storage = f'  "{self.folder / "saved"}"  '
        for saved, expected in (
            ({}, {"frontend_port": 4123, "backend_port": 8010}),
            ({"frontend_port": 5100}, {"frontend_port": 5100, "backend_port": 8010}),
            ({"backend_port": 5200}, {"frontend_port": 4123, "backend_port": 5200}),
        ):
            with self.subTest(saved=saved):
                preferences = {"data_root": storage, "future_option": {"enabled": True}, **saved}
                self.write_settings(preferences)
                self.assertEqual(user_settings.initialize_port_settings(self.port_mapping), expected)
                self.assertEqual(json.loads(self.settings_file.read_text(encoding="utf-8")),
                                 {**preferences, **expected})

    def test_existing_ports_are_not_rewritten_when_project_defaults_change(self):
        self.write_settings({"frontend_port": 5100, "backend_port": 5200, "future_option": True})
        before = self.settings_file.read_bytes()
        self.port_mapping.write_text('{"frontend_port": 6100, "backend_port": 6200}', encoding="utf-8")
        self.assertEqual(user_settings.initialize_port_settings(self.port_mapping),
                         {"frontend_port": 5100, "backend_port": 5200})
        self.assertEqual(self.settings_file.read_bytes(), before)

    def test_invalid_port_defaults_fail_before_creating_user_settings(self):
        invalid = ("{", "[]", "{}", '{"frontend_port": 4123}',
                   '{"frontend_port": 4123, "backend_port": 4123}',
                   '{"frontend_port": false, "backend_port": 8010}',
                   '{"frontend_port": "4123", "backend_port": 8010}',
                   '{"frontend_port": 4123.0, "backend_port": 8010}',
                   '{"frontend_port": 0, "backend_port": 8010}',
                   '{"frontend_port": 4123, "backend_port": 65536}')
        for raw in invalid:
            with self.subTest(raw=raw):
                self.port_mapping.write_text(raw, encoding="utf-8")
                self.assertRaises(user_settings.SettingsError, user_settings.initialize_port_settings,
                                  self.port_mapping)
                self.assertFalse(self.settings_file.parent.exists())
        self.port_mapping.unlink()
        self.assertRaises(user_settings.SettingsError, user_settings.initialize_port_settings,
                          self.port_mapping)
        self.assertFalse(self.settings_file.parent.exists())

    def test_invalid_saved_ports_and_json_are_never_replaced_or_partially_filled(self):
        self.settings_file.parent.mkdir(parents=True)
        invalid = (b"{broken", b"[]", b"null", b"\xff",
                   b'{"frontend_port":null,"other":"preserved"}',
                   b'{"frontend_port":true,"other":"preserved"}',
                   b'{"frontend_port":"4123","other":"preserved"}',
                   b'{"frontend_port":1.5,"other":"preserved"}',
                   b'{"frontend_port":0,"other":"preserved"}',
                   b'{"backend_port":65536,"other":"preserved"}',
                   b'{"frontend_port":8010,"other":"preserved"}',
                   b'{"frontend_port":5000,"backend_port":5000,"other":"preserved"}')
        for raw in invalid:
            with self.subTest(raw=raw):
                self.settings_file.write_bytes(raw)
                self.assertRaises(user_settings.SettingsError, user_settings.initialize_port_settings,
                                  self.port_mapping)
                self.assertEqual(self.settings_file.read_bytes(), raw)

    def test_port_initialization_rereads_persisted_preferences(self):
        self.write_settings({"frontend_port": 5100, "other": True})
        original_write = user_settings._write

        def updated_after_save(path, values, **kwargs):
            original_write(path, values, **kwargs)
            self.write_settings({**values, "backend_port": 5300})

        with patch.object(user_settings, "_write", side_effect=updated_after_save):
            self.assertEqual(user_settings.initialize_port_settings(self.port_mapping),
                             {"frontend_port": 5100, "backend_port": 5300})
        self.assertEqual(json.loads(self.settings_file.read_text(encoding="utf-8")),
                         {"frontend_port": 5100, "backend_port": 5300, "other": True})

    def test_port_update_failure_preserves_previous_preferences_and_cleans_staged_file(self):
        self.write_settings({"backend_port": 5200, "future_option": True})
        before = self.settings_file.read_bytes()
        with patch.object(user_settings.os, "replace", side_effect=PermissionError("blocked")):
            self.assertRaises(user_settings.SettingsError, user_settings.initialize_port_settings,
                              self.port_mapping)
        self.assertEqual(self.settings_file.read_bytes(), before)
        self.assertEqual(list(self.settings_file.parent.glob(".settings-*.tmp")), [])

    def test_saved_folder_survives_reads_and_empty_environment_overrides(self):
        saved = self.folder / "saved media"
        self.write_settings({"data_root": str(saved)})
        for override in ("", "  "):
            with self.subTest(override=override), patch.dict(os.environ, {"JOBS_ROOT": override}):
                self.assertEqual(user_settings.resolve_data_root(), saved)
        self.assertFalse(saved.exists())

    def test_explicit_environment_root_precedes_saved_folder(self):
        saved = self.folder / "saved"
        override = self.folder / "override"
        self.write_settings({"data_root": str(saved)})
        with patch.dict(os.environ, {"JOBS_ROOT": str(override)}):
            self.assertEqual(user_settings.resolve_data_root(), override)
        self.assertEqual(user_settings.read_settings()["data_root"], str(saved))

    def test_normalization_accepts_quoted_absolute_paths_and_expands_variables(self):
        destination = self.folder / "media files"
        self.assertEqual(user_settings.normalize_data_root(f'  "{destination}"  '),
                         str(destination))
        with patch.dict(os.environ, {"CLIPFEED_TEST_DATA": str(self.folder)}):
            self.assertEqual(user_settings.normalize_data_root("${CLIPFEED_TEST_DATA}/media files"),
                             str(destination))
        with patch.dict(os.environ, {"HOME": str(self.folder), "USERPROFILE": str(self.folder)}):
            self.assertEqual(user_settings.normalize_data_root("~/ClipFeedData"),
                             str(self.folder / "ClipFeedData"))
        for value in (None, "", "   ", '  ""  '):
            with self.subTest(value=value):
                self.assertIsNone(user_settings.normalize_data_root(value))

    def test_invalid_folder_values_are_rejected(self):
        for value in (123, True, [], "relative", "../media", "https://example.com/media",
                      str(self.folder) + "\n", str(self.folder) + "\x00",
                      str(self.folder) + "\x7f", "x" * 2049):
            with self.subTest(value=value):
                self.assertRaises(ValueError, user_settings.normalize_data_root, value)

    def test_bom_json_missing_storage_key_preserves_other_preferences(self):
        self.settings_file.parent.mkdir(parents=True)
        self.settings_file.write_text(json.dumps({"future_option": "café"}), encoding="utf-8-sig")
        self.assertEqual(user_settings.read_settings(), {"future_option": "café", "data_root": None})
        before = self.settings_file.read_bytes()
        user_settings.ensure_settings_file()
        self.assertEqual(self.settings_file.read_bytes(), before)

    def test_corrupt_or_invalid_existing_json_is_never_overwritten(self):
        self.settings_file.parent.mkdir(parents=True)
        invalid_files = (b"{broken", b"[]", b"null", b'{"data_root":42}',
                         b'{"data_root":"relative"}', b"\xff", b" " * (1024 * 1024 + 1))
        for raw in invalid_files:
            with self.subTest(sample=raw[:40]):
                self.settings_file.write_bytes(raw)
                self.assertRaises(user_settings.SettingsError, user_settings.read_settings)
                self.assertRaises(user_settings.SettingsError, user_settings.ensure_settings_file)
                self.assertRaises(user_settings.SettingsError, user_settings.update_storage,
                                  str(self.folder / "new-storage"))
                self.assertEqual(self.settings_file.read_bytes(), raw)

    def test_update_persists_folder_and_unknown_fields_without_switching_running_paths(self):
        self.write_settings({"data_root": None, "future_option": {"label": "café"}})
        destination = self.folder / "new storage"
        payload = user_settings.update_storage(str(destination))
        self.assertTrue(destination.is_dir())
        self.assertEqual(user_settings.read_settings(), {
            "data_root": str(destination), "future_option": {"label": "café"}})
        self.assertEqual(payload["effective_data_root"], str(destination))
        self.assertEqual(payload["active_data_root"], str(self.default_root))
        self.assertEqual(payload["active_database_path"], str(self.default_root / "jobs.db"))
        self.assertEqual(payload["active_media_root"], str(self.default_root / "library"))
        self.assertTrue(payload["restart_required"])
        self.assertFalse(payload["environment_override"])
        self.assertEqual(self.config.JOBS_ROOT, self.default_root)

    def test_clearing_saved_folder_restores_temp_default_after_restart(self):
        active = self.folder / "active"
        self.write_settings({"data_root": str(active)})
        with patch.object(self.config, "JOBS_ROOT", active):
            payload = user_settings.update_storage("")
            self.assertIsNone(payload["data_root"])
            self.assertEqual(payload["effective_data_root"], str(self.default_root))
            self.assertTrue(payload["restart_required"])
        self.assertEqual(user_settings.resolve_data_root(), self.default_root)
        self.assertTrue(self.default_root.is_dir())

    def test_environment_override_does_not_erase_saved_preference_or_request_restart(self):
        active = self.folder / "environment-storage"
        selected = self.folder / "selected"
        with patch.object(self.config, "JOBS_ROOT", active), \
             patch.object(self.config, "JOBS_ROOT_ENV_OVERRIDE", True):
            payload = user_settings.update_storage(str(selected))
            self.assertEqual(payload["data_root"], str(selected))
            self.assertEqual(payload["effective_data_root"], str(active))
            self.assertTrue(payload["environment_override"])
            self.assertFalse(payload["restart_required"])
        self.assertEqual(user_settings.resolve_data_root(), selected)

    def test_selecting_active_folder_does_not_request_restart(self):
        payload = user_settings.update_storage(str(self.default_root))
        self.assertFalse(payload["restart_required"])

    def test_destination_file_is_rejected_without_changing_preference(self):
        self.write_settings({"data_root": None})
        before = self.settings_file.read_bytes()
        destination = self.folder / "file.mp4"
        destination.write_bytes(b"video")
        self.assertRaises(ValueError, user_settings.update_storage, str(destination))
        self.assertEqual(self.settings_file.read_bytes(), before)
        self.assertEqual(destination.read_bytes(), b"video")

    def test_unwritable_destination_does_not_change_preference(self):
        self.write_settings({"data_root": None})
        before = self.settings_file.read_bytes()
        with patch.object(user_settings.tempfile, "TemporaryFile", side_effect=PermissionError("blocked")):
            self.assertRaises(OSError, user_settings.update_storage, str(self.folder / "blocked"))
        self.assertEqual(self.settings_file.read_bytes(), before)

    def test_atomic_replace_failure_preserves_previous_json_and_cleans_staged_file(self):
        self.write_settings({"data_root": None, "future_option": True})
        before = self.settings_file.read_bytes()
        with patch.object(user_settings.os, "replace", side_effect=PermissionError("blocked")):
            self.assertRaises(user_settings.SettingsError, user_settings.update_storage,
                              str(self.folder / "selected"))
        self.assertEqual(self.settings_file.read_bytes(), before)
        self.assertEqual(list(self.settings_file.parent.glob(".settings-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
