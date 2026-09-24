from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
import shutil
import sys
import types
import tempfile
import unittest
import urllib.error
from unittest import mock

from videotranslator.models.catalog import get_model
from videotranslator.models.manager_v8 import RuntimeModelManager
from videotranslator.translation.local.manager import LocalTranslationManager
from videotranslator.translation.local.marian_engine import MarianEngine
from videotranslator.translation.quality_checker import inspect
from videotranslator.core.settings_io import atomic_write_json, read_json
from videotranslator.media import process as media_process
from videotranslator.pipeline.tts_generate import TTSGenerateMixin, _piper_model_revision
from videotranslator.tts.piper import PiperVoice
from videotranslator.ui.models_tab import UIModelsMixin


class HybridQualityV9Tests(unittest.TestCase):
    def test_one_long_untranslated_source_word_requests_repair(self):
        result = inspect(
            "Configure authentication before deployment",
            "Настройте authentication перед развертыванием",
            "en",
            "ru",
        )
        self.assertFalse(result["ok"])
        self.assertIn("source_word_left", result["reasons"])

    def test_known_technical_terms_do_not_trigger_repair(self):
        result = inspect(
            "Use Python API with Docker and FFmpeg",
            "Используйте Python API с Docker и FFmpeg",
            "en",
            "ru",
        )
        self.assertTrue(result["ok"], result)

    def test_latin_source_languages_also_detect_leftover_words(self):
        result = inspect(
            "Configura la autenticación antes del despliegue",
            "Настройте autenticación перед despliegue",
            "es",
            "ru",
        )
        self.assertFalse(result["ok"])
        self.assertIn("source_words_left", result["reasons"])

    def test_distinct_source_script_leftover_requests_repair(self):
        result = inspect(
            "設定を保存してから続行してください",
            "Сохраните 設定 перед продолжением",
            "ja",
            "ru",
        )
        self.assertFalse(result["ok"])
        self.assertIn("source_script_left", result["reasons"])


class _BatchBackend:
    name = "batch_backend"

    def __init__(self):
        self.calls = 0

    def available(self, source, target):
        return True

    def translate_many(self, texts, source, target):
        self.calls += 1
        return [f"ru:{text}" for text in texts]


class LocalBatchTranslationTests(unittest.TestCase):
    def test_local_manager_uses_one_batch_call_when_backend_supports_it(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = LocalTranslationManager(root=directory)
            backend = _BatchBackend()
            manager.backends = [backend]
            results = manager.translate_many(["one", "two", "three"], "en", "ru")

        self.assertEqual(backend.calls, 1)
        self.assertEqual([item.text for item in results], ["ru:one", "ru:two", "ru:three"])


class _FailingBackend:
    name = "broken_local"

    def __init__(self):
        self.calls = 0

    def available(self, source, target):
        return True

    def translate_many(self, texts, source, target):
        self.calls += 1
        raise RuntimeError("model OOM")


class _WorkingFallbackBackend:
    name = "working_fallback"

    def __init__(self):
        self.calls = 0

    def available(self, source, target):
        return True

    def translate_many(self, texts, source, target):
        self.calls += 1
        return [f"локально:{text}" for text in texts]


class LocalBackendCircuitTests(unittest.TestCase):
    def test_failed_local_backend_is_not_retried_for_later_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = LocalTranslationManager(root=directory)
            broken = _FailingBackend()
            fallback = _WorkingFallbackBackend()
            manager.backends = [broken, fallback]
            first = manager.translate_many(["one", "two"], "en", "ru")
            second = manager.translate_many(["three"], "en", "ru")

        self.assertEqual(broken.calls, 1)
        self.assertEqual(fallback.calls, 2)
        self.assertEqual([row.text for row in first], ["локально:one", "локально:two"])
        self.assertEqual(second[0].text, "локально:three")
        self.assertIn("broken_local", manager._disabled_backends)




class ArgosRouteTests(unittest.TestCase):
    class Package:
        def __init__(self, source, target):
            self.from_code = source
            self.to_code = target

    def test_direct_argos_route_is_preferred(self):
        packages = [self.Package("es", "en"), self.Package("en", "ru"), self.Package("es", "ru")]
        plan = RuntimeModelManager._argos_install_plan("es", "ru", packages)
        self.assertEqual(plan, [("es", "ru")])

    def test_argos_falls_back_to_english_pivot(self):
        packages = [self.Package("es", "en"), self.Package("en", "ru")]
        plan = RuntimeModelManager._argos_install_plan("es", "ru", packages)
        self.assertEqual(plan, [("es", "en"), ("en", "ru")])


class _FakeResponse:
    def __init__(self, payload: bytes, *, status=206, headers=None):
        self.payload = payload
        self.status = status
        self.headers = {"Content-Length": str(len(payload)), **(headers or {})}
        self._read = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getcode(self):
        return self.status

    def read(self, _size=-1):
        if self._read:
            return b""
        self._read = True
        return self.payload


class ModelManagerReliabilityTests(unittest.TestCase):
    def test_download_resumes_existing_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            target = Path(directory) / "model.bin"
            part = target.with_suffix(".bin.part")
            part.write_bytes(b"abc")
            response = _FakeResponse(b"def", status=206, headers={"Content-Range": "bytes 3-5/6"})
            with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
                manager._download("https://example.invalid/model.bin", target)

            self.assertEqual(target.read_bytes(), b"abcdef")
            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_header("Range"), "bytes=3-")

    def test_server_ignoring_range_restarts_partial_file_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            target = Path(directory) / "model.bin"
            part = target.with_suffix(".bin.part")
            part.write_bytes(b"old-part")
            ranged = _FakeResponse(b"complete", status=200)
            full = _FakeResponse(b"complete", status=200)
            with mock.patch("urllib.request.urlopen", side_effect=[ranged, full]) as urlopen:
                manager._download("https://example.invalid/model.bin", target)
            self.assertEqual(target.read_bytes(), b"complete")
            self.assertEqual(urlopen.call_count, 2)

    def test_malformed_206_content_range_is_not_appended(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            target = Path(directory) / "model.bin"
            part = target.with_suffix(".bin.part")
            part.write_bytes(b"abc")
            wrong_partial = _FakeResponse(
                b"WRONG", status=206, headers={"Content-Range": "bytes 0-4/8"}
            )
            full = _FakeResponse(b"abcdefgh", status=200)
            with mock.patch("urllib.request.urlopen", side_effect=[wrong_partial, full]) as urlopen:
                manager._download("https://example.invalid/model.bin", target)
            self.assertEqual(target.read_bytes(), b"abcdefgh")
            self.assertEqual(urlopen.call_count, 2)

    def test_http_416_restarts_stale_partial_from_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            target = Path(directory) / "model.bin"
            part = target.with_suffix(".bin.part")
            part.write_bytes(b"stale-partial")
            range_error = urllib.error.HTTPError(
                "https://example.invalid/model.bin", 416, "Range Not Satisfiable", {}, None
            )
            full = _FakeResponse(b"fresh-complete", status=200)
            with mock.patch("urllib.request.urlopen", side_effect=[range_error, full]) as urlopen:
                manager._download("https://example.invalid/model.bin", target)
            self.assertEqual(target.read_bytes(), b"fresh-complete")
            self.assertEqual(urlopen.call_count, 2)

    def test_http_416_accepts_checksum_proven_complete_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            target = Path(directory) / "model.bin"
            part = target.with_suffix(".bin.part")
            payload = b"already-complete"
            part.write_bytes(payload)
            expected = hashlib.sha256(payload).hexdigest()
            range_error = urllib.error.HTTPError(
                "https://example.invalid/model.bin", 416, "Range Not Satisfiable", {}, None
            )
            with mock.patch("urllib.request.urlopen", side_effect=[range_error]) as urlopen:
                manager._download("https://example.invalid/model.bin", target, expected_sha256=expected)
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(part.exists())
            self.assertEqual(urlopen.call_count, 1)

    def test_hot_piper_voice_lookup_does_not_hash_model(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            (path / item["filename"]).write_bytes(b"fake-model")
            (path / item["config_filename"]).write_text("{}", encoding="utf-8")
            with mock.patch.object(manager, "_sha256", side_effect=AssertionError("hot path must not hash")):
                selected = manager.preferred_piper_voice_path("piper-dmitri-ru", "ru")
            self.assertEqual(selected, path / item["filename"])

    def test_piper_resolver_reports_actual_fallback_voice_id(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            preferred = get_model("piper-dmitri-ru")
            fallback = get_model("piper-irina-ru")
            path = manager._item_path(fallback)
            path.mkdir(parents=True, exist_ok=True)
            (path / fallback["filename"]).write_bytes(b"fake-model")
            (path / fallback["config_filename"]).write_text("{}", encoding="utf-8")

            resolved = manager.resolve_piper_voice(preferred["id"], "ru")

            self.assertIsNotNone(resolved)
            self.assertEqual(resolved[0], fallback["id"])
            self.assertEqual(resolved[1], path / fallback["filename"])

    def test_explicit_verify_still_checks_piper_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            (path / item["filename"]).write_bytes(b"fake-model")
            (path / item["config_filename"]).write_text("{}", encoding="utf-8")
            with mock.patch.object(manager, "_sha256", return_value=item["sha256"]) as sha:
                status = manager.verify(item["id"])
            self.assertEqual(status["integrity"], "ok")
            sha.assert_called_once()

    def test_explicit_verify_rejects_broken_piper_json_config(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            (path / item["filename"]).write_bytes(b"fake-model")
            (path / item["config_filename"]).write_text("{broken", encoding="utf-8")
            with mock.patch.object(manager, "_sha256", return_value=item["sha256"]):
                status = manager.verify(item["id"])
            self.assertEqual(status["integrity"], "config_invalid")

    def test_frozen_build_does_not_pretend_external_pip_becomes_importable(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            with mock.patch.object(sys, "frozen", True, create=True):
                with self.assertRaisesRegex(RuntimeError, "PyInstaller bundle"):
                    manager._python_command()

    def test_install_on_healthy_existing_voice_does_not_redownload_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            (path / item["filename"]).write_bytes(b"already-installed")
            (path / item["config_filename"]).write_text("{}", encoding="utf-8")
            with mock.patch.object(manager, "_sha256", return_value=item["sha256"]), \
                 mock.patch.object(manager, "_install_piper_voice", side_effect=AssertionError("must not redownload")):
                status = manager.install(item["id"])
            self.assertEqual(status["integrity"], "ok")
            self.assertTrue((path / "install_manifest.json").is_file())

    def test_install_repairs_corrupt_existing_voice_instead_of_false_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            model = path / item["filename"]
            config = path / item["config_filename"]
            model.write_bytes(b"corrupt")
            config.write_text("{}", encoding="utf-8")

            def fake_reinstall(_item, progress_cb=None):
                model.write_bytes(b"repaired")

            with mock.patch.object(manager, "_sha256", side_effect=["bad", item["sha256"]]), \
                 mock.patch.object(manager, "_install_piper_voice", side_effect=fake_reinstall) as reinstall:
                status = manager.install(item["id"])

            self.assertEqual(status["integrity"], "ok")
            reinstall.assert_called_once()

    def test_failed_voice_update_preserves_previous_working_directory_and_resumes_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            old_model = b"previous-working-model"
            old_config = '{"old": true}'
            (path / item["filename"]).write_bytes(old_model)
            (path / item["config_filename"]).write_text(old_config, encoding="utf-8")
            (path / "keep.me").write_text("old-install", encoding="utf-8")
            calls = []

            def interrupted_download(_url, target, expected_sha256=None, progress_cb=None):
                calls.append(target.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.name == item["filename"]:
                    target.write_bytes(b"new-model")
                    return target
                raise OSError("network interrupted before config")

            with mock.patch("importlib.util.find_spec", return_value=object()), \
                 mock.patch.object(manager, "_installed_distribution_version", return_value="1.8.0"), \
                 mock.patch.object(manager, "_download", side_effect=interrupted_download), \
                 mock.patch.object(manager, "_sha256", return_value=item["sha256"]):
                with self.assertRaisesRegex(OSError, "network interrupted"):
                    manager.update(item["id"])

            staging = manager._transaction_staging_path(item)
            self.assertEqual((path / item["filename"]).read_bytes(), old_model)
            self.assertEqual((path / item["config_filename"]).read_text(encoding="utf-8"), old_config)
            self.assertTrue((path / "keep.me").is_file())
            self.assertTrue((staging / item["filename"]).is_file())
            self.assertEqual(calls, [item["filename"], item["config_filename"]])

            def resume_download(_url, target, expected_sha256=None, progress_cb=None):
                calls.append(target.name)
                self.assertEqual(target.name, item["config_filename"])
                target.write_text('{"new": true}', encoding="utf-8")
                return target

            with mock.patch("importlib.util.find_spec", return_value=object()), \
                 mock.patch.object(manager, "_installed_distribution_version", return_value="1.8.0"), \
                 mock.patch.object(manager, "_download", side_effect=resume_download), \
                 mock.patch.object(manager, "_sha256", return_value=item["sha256"]):
                status = manager.update(item["id"])

            self.assertEqual(status["integrity"], "ok")
            self.assertEqual((path / item["filename"]).read_bytes(), b"new-model")
            self.assertEqual(calls[-1], item["config_filename"])
            self.assertFalse(staging.exists())

    def test_successful_voice_update_replaces_directory_only_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            (path / item["filename"]).write_bytes(b"old-model")
            (path / item["config_filename"]).write_text('{"old": true}', encoding="utf-8")
            (path / "obsolete.bin").write_bytes(b"obsolete")

            def fake_download(_url, target, expected_sha256=None, progress_cb=None):
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.name == item["filename"]:
                    target.write_bytes(b"new-model")
                else:
                    target.write_text('{"new": true}', encoding="utf-8")
                return target

            with mock.patch("importlib.util.find_spec", return_value=object()), \
                 mock.patch.object(manager, "_installed_distribution_version", return_value="1.8.0"), \
                 mock.patch.object(manager, "_download", side_effect=fake_download), \
                 mock.patch.object(manager, "_sha256", return_value=item["sha256"]):
                status = manager.update(item["id"])

            self.assertEqual(status["integrity"], "ok")
            self.assertEqual((path / item["filename"]).read_bytes(), b"new-model")
            self.assertEqual(json.loads((path / item["config_filename"]).read_text(encoding="utf-8")), {"new": True})
            self.assertFalse((path / "obsolete.bin").exists())
            self.assertTrue((path / "install_manifest.json").is_file())

    def test_installed_voice_is_adopted_without_redownload(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            (path / item["filename"]).write_bytes(b"already-installed")
            (path / item["config_filename"]).write_text("{}", encoding="utf-8")

            with mock.patch.object(manager, "_sha256", return_value=item["sha256"]), \
                 mock.patch.object(manager, "install", side_effect=AssertionError("must not redownload")):
                result = manager.update_if_needed(item["id"])

            self.assertEqual(result["maintenance_action"], "healthy")
            manifest = read_json(path / "install_manifest.json")
            self.assertEqual(manifest["model_id"], item["id"])
            self.assertEqual(manifest["catalog_fingerprint"], manager._catalog_fingerprint(item))

    def test_corrupt_installed_voice_is_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            (path / item["filename"]).write_bytes(b"corrupt")
            (path / item["config_filename"]).write_text("{}", encoding="utf-8")
            repaired = {
                "model_id": item["id"], "installed": True, "integrity": "ok",
                "size_mb": 63.3, "expected_mb": 64.0, "path": str(path), "detail": "",
            }
            with mock.patch.object(manager, "_sha256", return_value="bad-checksum"), \
                 mock.patch.object(manager, "install", return_value=repaired) as install:
                result = manager.update_if_needed(item["id"])

            self.assertEqual(result["maintenance_action"], "repaired")
            install.assert_called_once_with(item["id"], progress_cb=None, force=True)

    def test_manifested_partial_voice_is_repaired_by_maintenance_logic(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            manager._write_install_manifest(item)
            repaired = {
                "model_id": item["id"], "installed": True, "integrity": "ok",
                "size_mb": 63.3, "expected_mb": 64.0,
                "path": str(manager._item_path(item)), "detail": "",
            }
            with mock.patch.object(manager, "install", return_value=repaired) as install:
                result = manager.update_if_needed(item["id"])
            self.assertEqual(result["maintenance_action"], "repaired")
            install.assert_called_once_with(item["id"], progress_cb=None, force=True)

    def test_maintenance_timestamp_survives_manager_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            self.assertTrue(manager.maintenance_due())
            manager._mark_maintenance_checked()
            restarted = RuntimeModelManager(directory)
            self.assertFalse(restarted.maintenance_due())

    def test_startup_scan_is_quick_when_recent_full_integrity_check_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            manager._mark_maintenance_checked()
            result_payload = {"checked": True, "items": [], "errors": []}
            with mock.patch.object(manager, "scan_installed_updates", return_value=result_payload) as scan:
                result = manager.startup_scan_installed_updates()
            scan.assert_called_once_with(verify_integrity=False)
            self.assertEqual(result["scan_mode"], "quick")

    def test_startup_full_scan_marks_maintenance_only_when_healthy(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            healthy = {
                "checked": True,
                "items": [{"model_id": "piper-dmitri-ru", "action": "healthy"}],
                "errors": [],
            }
            with mock.patch.object(manager, "scan_installed_updates", return_value=healthy) as scan, \
                 mock.patch.object(manager, "_mark_maintenance_checked") as mark:
                result = manager.startup_scan_installed_updates()
            scan.assert_called_once_with(verify_integrity=True)
            mark.assert_called_once_with()
            self.assertEqual(result["scan_mode"], "full")

            repair = {
                "checked": True,
                "items": [{"model_id": "piper-dmitri-ru", "action": "repair_needed"}],
                "errors": [],
            }
            with mock.patch.object(manager, "scan_installed_updates", return_value=repair), \
                 mock.patch.object(manager, "_mark_maintenance_checked") as mark:
                manager.startup_scan_installed_updates()
            mark.assert_not_called()

    def test_catalog_argos_status_requires_direct_package_not_only_pivot_route(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            with mock.patch.object(manager, "_argos_direct_pair_installed", return_value=False) as direct, \
                 mock.patch.object(manager, "_argos_pair_installed", return_value=True) as route:
                status = manager.status("argos-en-ru")
            self.assertFalse(status["installed"])
            self.assertEqual(status["integrity"], "missing")
            direct.assert_called_once_with("en", "ru")
            route.assert_not_called()

    def test_quick_startup_scan_verifies_legacy_voice_before_manifest_adoption(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            (path / item["filename"]).write_bytes(b"corrupt-legacy")
            (path / item["config_filename"]).write_text("{}", encoding="utf-8")

            with mock.patch.object(manager, "_sha256", return_value="bad-checksum") as sha:
                result = manager.scan_installed_updates(verify_integrity=False)

            row = next(row for row in result["items"] if row["model_id"] == item["id"])
            self.assertEqual(row["action"], "repair_needed")
            self.assertFalse((path / "install_manifest.json").exists())
            sha.assert_called_once()

    def test_startup_scan_reports_catalog_update_without_downloading(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            (path / item["filename"]).write_bytes(b"voice")
            (path / item["config_filename"]).write_text("{}", encoding="utf-8")
            atomic_write_json(path / "install_manifest.json", {
                "schema": 1, "model_id": item["id"], "catalog_fingerprint": "old-catalog",
            })
            with mock.patch.object(manager, "_sha256", return_value=item["sha256"]), \
                 mock.patch.object(manager, "install", side_effect=AssertionError("scan must not install")):
                result = manager.scan_installed_updates()

            row = next(row for row in result["items"] if row["model_id"] == item["id"])
            self.assertEqual(row["action"], "update_available")
            self.assertTrue(row["can_update"])

    def test_model_store_process_lock_rejects_second_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            first = RuntimeModelManager(directory)
            second = RuntimeModelManager(directory)
            with first._process_store_lock():
                with self.assertRaisesRegex(RuntimeError, "другой запущенной копией"):
                    with second._process_store_lock():
                        pass

    def test_piper_engine_requires_matching_distribution_metadata_not_just_module_name(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            with mock.patch("importlib.util.find_spec", return_value=object()), \
                 mock.patch.object(manager, "_installed_distribution_version", return_value=""):
                status = manager.status("piper-engine")
            self.assertFalse(status["installed"])
            self.assertEqual(status["integrity"], "metadata_missing")
            self.assertIn("metadata", status["detail"])

    def test_piper_engine_install_does_not_trust_unrelated_piper_module(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-engine")
            with mock.patch("importlib.util.find_spec", return_value=object()), \
                 mock.patch.object(manager, "_installed_distribution_version", return_value=""), \
                 mock.patch.object(manager, "_pip_install") as pip_install:
                manager._install_piper_engine(item)
            pip_install.assert_called_once_with(item["pip"], progress_cb=None)

    def test_piper_engine_force_update_reinstalls_pinned_package_in_source_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-engine")
            with mock.patch("importlib.util.find_spec", return_value=object()), \
                 mock.patch.object(manager, "_installed_distribution_version", return_value="1.8.0"), \
                 mock.patch.object(manager, "_pip_install") as pip_install:
                manager._install_piper_engine(item, force=True)
            pip_install.assert_called_once_with("--upgrade", item["pip"], progress_cb=None)

    def test_frozen_startup_scan_does_not_offer_runtime_engine_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-engine")
            manager._write_install_manifest(item)
            with mock.patch("importlib.util.find_spec", return_value=object()), \
                 mock.patch.object(manager, "_catalog_changed", return_value=True), \
                 mock.patch.object(manager, "_piper_engine_version_state", return_value=("1.8.0", "1.9.0", True)), \
                 mock.patch.object(sys, "frozen", True, create=True):
                result = manager.scan_installed_updates(verify_integrity=False)
            row = next(row for row in result["items"] if row["model_id"] == item["id"])
            self.assertEqual(row["action"], "app_update_required")
            self.assertFalse(row["can_update"])

    def test_marian_deep_status_detects_broken_auxiliary_json(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("marian-en-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            for name in manager.MARIAN_FILES:
                if name == "vocab.json":
                    (path / name).write_text("{broken", encoding="utf-8")
                elif name.endswith(".json"):
                    (path / name).write_text("{}", encoding="utf-8")
                else:
                    (path / name).write_bytes(b"x")
            status = manager.status(item["id"], verify_checksum=True)
            self.assertTrue(status["installed"])
            self.assertEqual(status["integrity"], "config_invalid")
            self.assertIn("vocab.json", status["detail"])

    def test_marian_staging_validation_rejects_broken_auxiliary_json(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            path = Path(directory) / "marian-staging"
            path.mkdir(parents=True, exist_ok=True)
            for name in manager.MARIAN_FILES:
                if name == "tokenizer_config.json":
                    (path / name).write_text("{broken", encoding="utf-8")
                elif name.endswith(".json"):
                    (path / name).write_text("{}", encoding="utf-8")
                else:
                    (path / name).write_bytes(b"x")
            with self.assertRaisesRegex(RuntimeError, "tokenizer_config.json"):
                manager._validate_marian_directory(path)

    def test_marian_missing_auxiliary_file_is_partial_not_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("marian-en-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            for name in manager.MARIAN_FILES:
                if name != "vocab.json":
                    (path / name).write_bytes(b"{}" if name.endswith(".json") else b"x")
            status = manager.status(item["id"], verify_checksum=True)
            self.assertFalse(status["installed"])
            self.assertEqual(status["integrity"], "partial")

    def test_piper_resolver_skips_voice_with_invalid_json_config(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            bad = get_model("piper-dmitri-ru")
            good = get_model("piper-irina-ru")
            bad_path = manager._item_path(bad)
            good_path = manager._item_path(good)
            bad_path.mkdir(parents=True, exist_ok=True)
            good_path.mkdir(parents=True, exist_ok=True)
            (bad_path / bad["filename"]).write_bytes(b"bad-model")
            (bad_path / bad["config_filename"]).write_text("{broken", encoding="utf-8")
            (good_path / good["filename"]).write_bytes(b"good-model")
            (good_path / good["config_filename"]).write_text("{}", encoding="utf-8")
            resolved = manager.resolve_piper_voice(bad["id"], "ru")
            self.assertEqual(resolved[0], good["id"])

    def test_manager_recovers_previous_directory_after_interrupted_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            backup = manager._transaction_backup_path(item)
            path.mkdir(parents=True, exist_ok=True)
            (path / "working.bin").write_bytes(b"old-working-version")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.replace(backup)

            restarted = RuntimeModelManager(directory)

            self.assertTrue((restarted._item_path(item) / "working.bin").is_file())
            self.assertFalse(backup.exists())

    def test_interrupted_commit_keeps_valid_staging_marker_for_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            target = manager._item_path(item)
            staging = manager._transaction_staging_path(item)
            target.mkdir(parents=True, exist_ok=True)
            (target / "old.bin").write_bytes(b"working")
            real_replace = os.replace

            def build(path):
                (path / "new.bin").write_bytes(b"downloaded")

            def replace_with_commit_failure(src, dst):
                if Path(src) == staging and Path(dst) == target:
                    raise OSError("commit interrupted")
                return real_replace(src, dst)

            with mock.patch("videotranslator.models.manager_v8.os.replace", side_effect=replace_with_commit_failure):
                with self.assertRaisesRegex(OSError, "commit interrupted"):
                    manager._transactional_directory_install(item, build, lambda _path: None)

            self.assertTrue((target / "old.bin").is_file())
            self.assertTrue((staging / "new.bin").is_file())
            self.assertTrue((staging / ".staging_manifest.json").is_file())

            restarted = RuntimeModelManager(directory)
            resumed = restarted._prepare_transaction_staging(item)
            self.assertEqual(resumed, staging)
            self.assertTrue((resumed / "new.bin").is_file())

    def test_successful_commit_does_not_fail_when_old_backup_cleanup_is_temporarily_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            target = manager._item_path(item)
            backup = manager._transaction_backup_path(item)
            target.mkdir(parents=True, exist_ok=True)
            (target / "old.bin").write_bytes(b"working")
            real_rmtree = shutil.rmtree

            def build(path):
                (path / "new.bin").write_bytes(b"new-version")

            def locked_backup_cleanup(path, *args, **kwargs):
                if Path(path) == backup:
                    raise PermissionError("old model still locked")
                return real_rmtree(path, *args, **kwargs)

            with mock.patch("videotranslator.models.manager_v8.shutil.rmtree", side_effect=locked_backup_cleanup):
                manager._transactional_directory_install(item, build, lambda _path: None)

            self.assertTrue((target / "new.bin").is_file())
            self.assertTrue(backup.exists())
            self.assertFalse((target / ".staging_manifest.json").exists())

            RuntimeModelManager(directory)
            self.assertFalse(backup.exists())

    def test_remove_cleans_resumable_staging_data_too(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("piper-dmitri-ru")
            path = manager._item_path(item)
            staging = manager._transaction_staging_path(item)
            path.mkdir(parents=True, exist_ok=True)
            staging.mkdir(parents=True, exist_ok=True)
            (path / "active.bin").write_bytes(b"active")
            (staging / "partial.bin.part").write_bytes(b"partial")

            removed = manager.remove(item["id"])

            self.assertTrue(removed)
            self.assertFalse(path.exists())
            self.assertFalse(staging.exists())

    def test_remove_does_not_report_success_when_filesystem_refuses_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("marian-en-ru")
            path = manager._item_path(item)
            path.mkdir(parents=True, exist_ok=True)
            (path / "sentinel.bin").write_bytes(b"keep")

            with mock.patch("videotranslator.models.manager_v8.shutil.rmtree", side_effect=PermissionError("locked")):
                with self.assertRaisesRegex(PermissionError, "locked"):
                    manager.remove(item["id"])

            self.assertTrue(path.exists())

    def test_default_model_root_can_be_moved_without_using_app_folder(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.dict("os.environ", {"VIDEO_TRANSLATOR_MODELS_DIR": directory}, clear=False):
            manager = RuntimeModelManager()
            self.assertEqual(manager.root, Path(directory))


class _FakeTTSCache:
    def __init__(self):
        self.keys = []
        self.metadata = []

    def make_key(self, *args):
        self.keys.append(args)
        return f"key-{len(self.keys)}"

    @contextmanager
    def key_lock(self, _key):
        yield

    def restore(self, _key, _path):
        return False

    def store(self, _key, _path, metadata):
        self.metadata.append(dict(metadata))


class _BypassGuard:
    def claim_probe_or_bypass(self):
        return "bypass"


class _PiperHarness(TTSGenerateMixin):
    def __init__(self):
        self.target_info = {"code": "ru"}
        self.hybrid_translation_settings = {
            "local_piper_fallback": True,
            "preferred_piper_voice": "piper-dmitri-ru",
        }
        self.tts_cache = _FakeTTSCache()
        self.edge_network_guard = _BypassGuard()
        self._tts_allow_gtts_fallback = False
        self._piper_model_selection = None
        self._piper_voice_engine = None
        self.problems = []

    def _check_cancel(self):
        return None

    def _increment_tts_stat(self, _name):
        return 1

    def _set_final_tts_provider(self, _segment_index, _provider):
        return None

    def _problem(self, event, **details):
        self.problems.append((event, details))

    def log(self, _message):
        return None


class PiperFallbackCacheTests(unittest.TestCase):
    def test_fallback_voice_cache_key_uses_actual_voice_not_missing_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "irina.onnx"
            model_path.write_bytes(b"model")
            output = Path(directory) / "out.wav"

            class FakeManager:
                def resolve_piper_voice(self, preferred_id, language="ru"):
                    self.request = (preferred_id, language)
                    return "piper-irina-ru", model_path

            class FakePiper:
                def synthesize(self, text, model, destination):
                    Path(destination).write_bytes(b"w" * 1200)

            harness = _PiperHarness()
            with mock.patch("videotranslator.pipeline.tts_generate.RuntimeModelManager", return_value=FakeManager()), \
                 mock.patch("videotranslator.pipeline.tts_generate.PiperVoice", return_value=FakePiper()):
                provider = harness.generate_tts("Проверка локального голоса", str(output), "ru-RU-DmitryNeural")

            self.assertEqual(provider, "piper")
            piper_keys = [args for args in harness.tts_cache.keys if len(args) >= 4 and args[3] == "piper"]
            self.assertEqual(len(piper_keys), 1)
            self.assertEqual(piper_keys[0][1], "piper-irina-ru")
            self.assertEqual(harness.tts_cache.metadata[0]["voice_model"], "piper-irina-ru")
            self.assertEqual(harness.tts_cache.metadata[0]["requested_voice_model"], "piper-dmitri-ru")
            self.assertTrue(any(event == "tts_piper_voice_substituted" for event, _ in harness.problems))


    def test_piper_cache_revision_changes_when_voice_file_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "voice.onnx"
            config = Path(str(model) + ".json")
            model.write_bytes(b"v1")
            config.write_text("{}", encoding="utf-8")
            first = _piper_model_revision(model)
            model.write_bytes(b"voice-version-two")
            second = _piper_model_revision(model)
            self.assertNotEqual(first, second)

    def test_broken_local_piper_voice_automatically_tries_next_installed_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            first_model = Path(directory) / "dmitri.onnx"
            second_model = Path(directory) / "irina.onnx"
            first_model.write_bytes(b"first-model")
            second_model.write_bytes(b"second-model")
            output = Path(directory) / "out.wav"

            class FakeManager:
                def installed_piper_voices(self, preferred_id, language="ru"):
                    return [
                        ("piper-dmitri-ru", first_model),
                        ("piper-irina-ru", second_model),
                    ]

            class FakePiper:
                def synthesize(self, text, model, destination):
                    if Path(model) == first_model:
                        raise RuntimeError("broken first voice")
                    Path(destination).write_bytes(b"w" * 1200)

            harness = _PiperHarness()
            with mock.patch("videotranslator.pipeline.tts_generate.RuntimeModelManager", return_value=FakeManager()), \
                 mock.patch("videotranslator.pipeline.tts_generate.PiperVoice", return_value=FakePiper()):
                provider = harness.generate_tts(
                    "Проверка автоматического локального резерва",
                    str(output),
                    "ru-RU-DmitryNeural",
                )

            self.assertEqual(provider, "piper")
            self.assertTrue(output.exists())
            self.assertEqual(harness.tts_cache.metadata[-1]["voice_model"], "piper-irina-ru")
            self.assertEqual(
                harness._last_piper_provider_identity["resolved_voice_id"],
                "piper-irina-ru",
            )
            self.assertTrue(
                any(event == "tts_piper_voice_failed_trying_next" for event, _ in harness.problems)
            )


class RuntimeAdapterAvailabilityTests(unittest.TestCase):
    def test_piper_adapter_rejects_unrelated_module_without_piper_tts_metadata(self):
        adapter = PiperVoice()
        with mock.patch("videotranslator.tts.piper.importlib.util.find_spec", return_value=object()), \
             mock.patch("videotranslator.tts.piper.importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
            self.assertFalse(adapter.available())

    def test_piper_adapter_requires_valid_sidecar_config_for_voice(self):
        adapter = PiperVoice()
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "voice.onnx"
            config = Path(str(model) + ".json")
            model.write_bytes(b"voice")
            config.write_text("{broken", encoding="utf-8")
            with mock.patch("videotranslator.tts.piper.importlib.util.find_spec", return_value=object()), \
                 mock.patch("videotranslator.tts.piper.importlib.metadata.version", return_value="1.8.0"):
                self.assertFalse(adapter.available(model))
                config.write_text("{}", encoding="utf-8")
                self.assertTrue(adapter.available(model))

    def test_marian_adapter_rejects_incomplete_or_corrupt_managed_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name in MarianEngine.REQUIRED_FILES:
                (path / name).write_bytes(b"{}" if name.endswith(".json") else b"x")
            engine = MarianEngine(path)
            self.assertTrue(engine.available("en", "ru"))
            (path / "tokenizer_config.json").write_text("{broken", encoding="utf-8")
            self.assertFalse(engine.available("en", "ru"))


class PiperRuntimeReloadTests(unittest.TestCase):
    def test_same_voice_path_reloads_after_model_file_changes(self):
        loaded = []

        class FakeRuntimeVoice:
            @classmethod
            def load(cls, model_path):
                token = object()
                loaded.append((model_path, token))
                return token

        fake_piper = types.ModuleType("piper")
        fake_piper.PiperVoice = FakeRuntimeVoice
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "voice.onnx"
            config = Path(str(model) + ".json")
            model.write_bytes(b"v1")
            config.write_text("{}", encoding="utf-8")
            adapter = PiperVoice()
            with mock.patch.dict(sys.modules, {"piper": fake_piper}):
                first = adapter._load(model)
                self.assertIs(first, adapter._load(model))
                model.write_bytes(b"version-two-longer")
                second = adapter._load(model)
            self.assertIsNot(first, second)
            self.assertEqual(len(loaded), 2)


class _FakeButton:
    def __init__(self):
        self.state = None

    def config(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]


class ModelUiConcurrencyTests(unittest.TestCase):
    def test_model_buttons_and_start_stay_disabled_while_video_is_processing(self):
        harness = type("Harness", (UIModelsMixin,), {})()
        harness._processing = True
        harness._model_action_running = False
        for name in ("btn_model_install", "btn_model_remove", "btn_model_verify", "btn_model_update", "btn_model_refresh", "btn_start"):
            setattr(harness, name, _FakeButton())

        harness._refresh_model_button_state()

        self.assertEqual(harness.btn_start.state, "disabled")
        self.assertEqual(harness.btn_model_remove.state, "disabled")

    def test_finishing_model_action_does_not_enable_start_during_video_job(self):
        harness = type("Harness", (UIModelsMixin,), {})()
        harness._processing = True
        for name in ("btn_model_install", "btn_model_remove", "btn_model_verify", "btn_model_update", "btn_model_refresh", "btn_start"):
            setattr(harness, name, _FakeButton())

        harness._set_model_buttons_busy(True)
        harness._set_model_buttons_busy(False)

        self.assertFalse(harness._model_action_running)
        self.assertEqual(harness.btn_start.state, "disabled")
        self.assertEqual(harness.btn_model_install.state, "disabled")


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def config(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]


class StartupUpdatePromptTests(unittest.TestCase):
    def _harness(self):
        harness = type("Harness", (UIModelsMixin,), {})()
        harness.lbl_model_action = _FakeLabel()
        harness.runtime_model_manager = mock.Mock()
        harness._processing = False
        harness._model_action_running = True
        harness.refresh_ai_models = mock.Mock()
        harness._problem = mock.Mock()
        for name in ("btn_model_install", "btn_model_remove", "btn_model_verify", "btn_model_update", "btn_model_refresh", "btn_start"):
            setattr(harness, name, _FakeButton())
        return harness

    def test_user_can_decline_startup_update_without_any_download(self):
        harness = self._harness()
        result = {
            "checked": True,
            "errors": [],
            "items": [{
                "model_id": "piper-dmitri-ru",
                "name": "Piper Дмитрий",
                "action": "update_available",
                "can_update": True,
                "size_download_mb": 63.3,
            }],
        }
        with mock.patch("videotranslator.ui.models_tab.messagebox.askyesno", return_value=False) as prompt:
            harness._finish_startup_model_scan(result)
        prompt.assert_called_once()
        harness.runtime_model_manager.update.assert_not_called()
        self.assertFalse(harness._model_action_running)
        self.assertIn("отложено", harness.lbl_model_action.text.lower())

    def test_startup_scan_with_no_updates_never_prompts_or_updates(self):
        harness = self._harness()
        result = {
            "checked": True,
            "errors": [],
            "items": [{
                "model_id": "piper-dmitri-ru",
                "name": "Piper Дмитрий",
                "action": "healthy",
                "can_update": True,
                "size_download_mb": 63.3,
            }],
        }
        with mock.patch("videotranslator.ui.models_tab.messagebox.askyesno") as prompt:
            harness._finish_startup_model_scan(result)
        prompt.assert_not_called()
        harness.runtime_model_manager.update.assert_not_called()
        self.assertFalse(harness._model_action_running)
        self.assertIn("не требуются", harness.lbl_model_action.text.lower())


class SettingsPersistenceTests(unittest.TestCase):
    def test_settings_write_is_atomic_and_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            atomic_write_json(path, {"voice": "ru-RU-DmitryNeural", "value": 3})
            self.assertEqual(read_json(path)["value"], 3)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_failed_replace_keeps_previous_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"stable": true}\n', encoding="utf-8")
            with mock.patch("videotranslator.core.settings_io.os.replace", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    atomic_write_json(path, {"stable": False})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"stable": True})
            self.assertFalse(path.with_name(path.name + ".tmp").exists())


class FFmpegResolutionTests(unittest.TestCase):
    def test_application_local_ffmpeg_wins_over_global_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "ffmpeg.exe"
            local.write_bytes(b"local")
            with mock.patch.object(media_process, "get_bundled_resource_dir", return_value=root), \
                 mock.patch.object(media_process, "get_program_dir", return_value=root), \
                 mock.patch.object(media_process.shutil, "which", return_value=r"C:\\old\\ffmpeg.exe") as which:
                selected = media_process.find_ffmpeg()
            self.assertEqual(selected, str(local))
            which.assert_not_called()

    def test_pyinstaller_bundle_ffmpeg_wins_over_launcher_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bundle = base / "_internal"
            launcher = base / "app"
            bundle.mkdir(); launcher.mkdir()
            bundled = bundle / "ffmpeg.exe"
            bundled.write_bytes(b"bundled")
            (launcher / "ffmpeg.exe").write_bytes(b"external")
            with mock.patch.object(media_process, "get_bundled_resource_dir", return_value=bundle), \
                 mock.patch.object(media_process, "get_program_dir", return_value=launcher), \
                 mock.patch.object(media_process.shutil, "which", return_value=None):
                selected = media_process.find_ffmpeg()
            self.assertEqual(selected, str(bundled))




class MarianInstallDiskUsageTests(unittest.TestCase):
    def test_marian_downloads_into_staging_then_commits_managed_directory(self):
        calls = []

        def fake_download(**kwargs):
            calls.append(dict(kwargs))
            local = Path(kwargs["local_dir"])
            local.mkdir(parents=True, exist_ok=True)
            target = local / kwargs["filename"]
            target.write_bytes(b"{}" if kwargs["filename"].endswith(".json") else b"x")
            return str(target)

        fake_hf = types.ModuleType("huggingface_hub")
        fake_hf.hf_hub_download = fake_download
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("marian-en-ru")
            final_dir = manager._item_path(item)
            with mock.patch("importlib.util.find_spec", return_value=object()), \
                 mock.patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
                manager._install_marian(item)

            self.assertTrue(calls)
            staging_dirs = {call.get("local_dir") for call in calls}
            self.assertEqual(len(staging_dirs), 1)
            staging_dir = next(iter(staging_dirs))
            self.assertNotEqual(staging_dir, str(final_dir))
            self.assertEqual(Path(staging_dir).parent, final_dir.parent)
            self.assertTrue((final_dir / "pytorch_model.bin").is_file())
            self.assertTrue((final_dir / "config.json").is_file())
            self.assertFalse(Path(staging_dir).exists())

    def test_failed_marian_update_preserves_previous_working_directory(self):
        calls = []

        def fake_download(**kwargs):
            calls.append(dict(kwargs))
            local = Path(kwargs["local_dir"])
            local.mkdir(parents=True, exist_ok=True)
            target = local / kwargs["filename"]
            if kwargs["filename"] == "pytorch_model.bin":
                raise OSError("hub disconnected")
            target.write_bytes(b"{}" if kwargs["filename"].endswith(".json") else b"x")
            return str(target)

        fake_hf = types.ModuleType("huggingface_hub")
        fake_hf.hf_hub_download = fake_download
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeModelManager(directory)
            item = get_model("marian-en-ru")
            final_dir = manager._item_path(item)
            final_dir.mkdir(parents=True, exist_ok=True)
            (final_dir / "old-model.bin").write_bytes(b"working")
            with mock.patch("importlib.util.find_spec", return_value=object()), \
                 mock.patch.dict(sys.modules, {"huggingface_hub": fake_hf}):
                with self.assertRaisesRegex(OSError, "hub disconnected"):
                    manager._install_marian(item)

            self.assertTrue((final_dir / "old-model.bin").is_file())
            staging = manager._transaction_staging_path(item)
            self.assertTrue(staging.exists())
            self.assertTrue((staging / ".staging_manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
