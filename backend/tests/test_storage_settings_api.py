"""Storage preferences validate at the API boundary and report persistence failures."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers.settings import router
from app.utils import user_settings


class StorageSettingsApiTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))
        folder = Path(__file__).resolve().parent / "storage-fixture"
        self.payload = {
            "data_root": None,
            "default_data_root": str(folder / "default"),
            "active_data_root": str(folder / "default"),
            "effective_data_root": str(folder / "default"),
            "settings_path": str(folder / "settings.json"),
            "restart_required": False,
            "environment_override": False,
            "active_database_path": str(folder / "default" / "jobs.db"),
            "active_media_root": str(folder / "default" / "library"),
        }

    def test_get_returns_saved_and_running_storage_paths(self):
        with patch.object(user_settings, "storage_payload", return_value=self.payload) as read:
            response = self.client.get("/api/settings/storage")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), self.payload)
        read.assert_called_once_with()

    def test_patch_returns_persisted_folder_and_restart_status(self):
        folder = str(Path(self.payload["default_data_root"]).parent / "new downloads")
        saved = {**self.payload, "data_root": folder,
                 "effective_data_root": folder, "restart_required": True}
        with patch.object(user_settings, "update_storage", return_value=saved) as update:
            response = self.client.patch("/api/settings/storage", json={"data_root": folder})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), saved)
        update.assert_called_once_with(folder)

    def test_null_explicitly_restores_default_storage(self):
        with patch.object(user_settings, "update_storage", return_value=self.payload) as update:
            response = self.client.patch("/api/settings/storage", json={"data_root": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), self.payload)
        update.assert_called_once_with(None)

    def test_invalid_request_shapes_do_not_reach_persistence(self):
        invalid = ({}, {"data_root": False}, {"data_root": 123}, {"data_root": []},
                   {"data_root": {}}, {"data_root": "x" * 2049},
                   {"data_root": "download\x00folder"}, {"data_root": "downloads\n"},
                   {"data_root": "downloads\x7f"},
                   {"data_root": None, "extra": True})
        with patch.object(user_settings, "update_storage") as update:
            for body in invalid:
                with self.subTest(body=body):
                    response = self.client.patch("/api/settings/storage", json=body)
                    self.assertEqual(response.status_code, 422, response.text)
            update.assert_not_called()

    def test_invalid_folder_is_reported_as_validation_error(self):
        with patch.object(user_settings, "update_storage",
                          side_effect=ValueError("Enter an absolute data storage folder.")):
            response = self.client.patch("/api/settings/storage", json={"data_root": "downloads"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"], "Enter an absolute data storage folder.")

    def test_read_failure_reports_actionable_service_error(self):
        for failure in (OSError("Permission denied"),
                        user_settings.SettingsError("Invalid settings JSON")):
            with self.subTest(failure=failure), patch.object(
                    user_settings, "storage_payload", side_effect=failure):
                response = self.client.get("/api/settings/storage")
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn(str(failure), response.json()["detail"])
                self.assertIn("settings JSON and folder permissions", response.json()["detail"])

    def test_write_failure_reports_actionable_service_error(self):
        for failure in (OSError("Disk full"),
                        user_settings.SettingsError("Settings JSON is malformed")):
            with self.subTest(failure=failure), patch.object(
                    user_settings, "update_storage", side_effect=failure):
                response = self.client.patch("/api/settings/storage", json={"data_root": None})
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn(str(failure), response.json()["detail"])
                self.assertIn("settings JSON and folder permissions", response.json()["detail"])


class StorageSettingsPersistenceApiTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent)))
        self.enterContext(patch.dict(os.environ, {
            "APPDATA": str(self.folder / "appdata"),
            "XDG_CONFIG_HOME": str(self.folder / "appdata"),
        }))
        os.environ.pop("CLIPFEED_SETTINGS_PATH", None)
        if sys.platform == "darwin":
            os.environ["CLIPFEED_SETTINGS_PATH"] = str(
                self.folder / "appdata" / user_settings.APP_NAME / "settings.json")
        os.environ.pop("JOBS_ROOT", None)
        self.default_root = self.folder / "default" / "prod_jobs"
        self.enterContext(patch.object(user_settings, "default_data_root",
                                       return_value=self.default_root))
        self.enterContext(patch.object(config, "JOBS_ROOT", self.default_root))
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.default_root / "jobs.db"))
        self.enterContext(patch.object(config, "MEDIA_ROOT", self.default_root / "library"))
        self.enterContext(patch.object(config, "JOBS_ROOT_ENV_OVERRIDE", False))
        user_settings.ensure_settings_file()
        self.settings_path = user_settings.settings_path()
        app = FastAPI()
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))

    def test_settings_json_is_in_appdata_and_starts_with_default_storage(self):
        self.assertTrue(self.settings_path.is_relative_to(self.folder / "appdata"))
        self.assertEqual(self.settings_path.name, "settings.json")
        self.assertIsNone(json.loads(self.settings_path.read_text(encoding="utf-8"))["data_root"])
        response = self.client.get("/api/settings/storage")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["data_root"])
        self.assertEqual(response.json()["default_data_root"], str(self.default_root))
        self.assertEqual(response.json()["active_data_root"], str(self.default_root))
        self.assertEqual(response.json()["effective_data_root"], str(self.default_root))
        self.assertFalse(response.json()["restart_required"])

    def test_folder_persists_without_switching_running_database_or_media(self):
        saved_settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        saved_settings["other_preference"] = {"preserved": True}
        self.settings_path.write_text(json.dumps(saved_settings), encoding="utf-8")
        new_root = self.folder / "new downloads"
        response = self.client.patch("/api/settings/storage", json={
            "data_root": f'  "{new_root}"  '})
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["data_root"], str(new_root))
        self.assertEqual(saved["effective_data_root"], str(new_root))
        self.assertEqual(saved["active_data_root"], str(self.default_root))
        self.assertEqual(saved["active_database_path"], str(self.default_root / "jobs.db"))
        self.assertEqual(saved["active_media_root"], str(self.default_root / "library"))
        self.assertTrue(saved["restart_required"])
        self.assertTrue(new_root.is_dir())
        document = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(document["data_root"], str(new_root))
        self.assertEqual(document["other_preference"], {"preserved": True})
        self.assertEqual(self.client.get("/api/settings/storage").json(), saved)
        restored = self.client.patch("/api/settings/storage", json={"data_root": None})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertIsNone(restored.json()["data_root"])
        self.assertEqual(restored.json()["effective_data_root"], str(self.default_root))
        self.assertFalse(restored.json()["restart_required"])
        self.assertTrue(new_root.is_dir())

    def test_invalid_paths_do_not_replace_saved_json(self):
        before = self.settings_path.read_bytes()
        existing_file = self.folder / "file.txt"
        existing_file.write_text("Existing file", encoding="utf-8")
        for value in ("relative/downloads", "../downloads", "https://example.com/downloads",
                      str(existing_file)):
            with self.subTest(value=value):
                response = self.client.patch("/api/settings/storage", json={"data_root": value})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.settings_path.read_bytes(), before)

    def test_invalid_existing_json_reports_error_and_survives_update(self):
        self.settings_path.write_text("{broken json", encoding="utf-8")
        response = self.client.get("/api/settings/storage")
        self.assertEqual(response.status_code, 503, response.text)
        response = self.client.patch("/api/settings/storage", json={"data_root": None})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.settings_path.read_text(encoding="utf-8"), "{broken json")


if __name__ == "__main__":
    unittest.main()
