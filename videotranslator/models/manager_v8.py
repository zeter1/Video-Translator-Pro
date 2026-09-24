"""Runtime installer/verifier for optional local translation and TTS models.

Large downloads are always explicit UI actions, except Argos language pairs that
may be installed on demand when the user enabled auto-install in settings.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import importlib.util
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request

from videotranslator.core.settings_io import atomic_write_json, read_json
from videotranslator.core.process_flags import no_console_creationflags
from videotranslator.models.catalog import MARIAN_MODEL_FILES, get_catalog, get_model


@dataclass
class ModelStatus:
    model_id: str
    installed: bool
    size_mb: float
    expected_mb: float
    integrity: str
    path: str = ""
    detail: str = ""


class RuntimeModelManager:
    _install_lock = threading.RLock()
    AUTO_MAINTENANCE_DAYS = 7
    # Backwards-compatible class alias used by verification/tests; canonical
    # ownership lives in models.catalog and is shared with MarianEngine.
    MARIAN_FILES = MARIAN_MODEL_FILES

    def __init__(self, root=None):
        # Model data deliberately lives outside the application/install folder.
        # Replacing the source tree or installing a newer EXE therefore does not
        # remove already downloaded voices/models.  Tests may still pass an
        # isolated root explicitly.
        configured_root = os.environ.get("VIDEO_TRANSLATOR_MODELS_DIR") if root is None else None
        self.root = Path(root or configured_root or Path.home() / ".video_translator_models").expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self._maintenance_state_path = self.root / "manager_state.json"
        # File locks are not reliably re-entrant (notably on Windows). Public
        # manager methods may call another protected manager method, so remember
        # ownership for this manager/thread while the outer OS lock remains held.
        self._store_lock_local = threading.local()
        try:
            with self._process_store_lock():
                self._recover_interrupted_transactions()
        except RuntimeError:
            # Another running application owns the store right now. Its active
            # transaction remains authoritative; this instance must not interfere.
            pass

    @contextmanager
    def _process_store_lock(self):
        """Prevent two application processes from mutating one model store."""
        depth = int(getattr(self._store_lock_local, "depth", 0) or 0)
        if depth:
            self._store_lock_local.depth = depth + 1
            try:
                yield
            finally:
                self._store_lock_local.depth = depth
            return

        lock_path = self.root / ".manager.lock"
        handle = lock_path.open("a+b")
        locked = False
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise RuntimeError(
                        "Хранилище локальных моделей занято другой запущенной копией программы."
                    ) from exc
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise RuntimeError(
                        "Хранилище локальных моделей занято другой запущенной копией программы."
                    ) from exc
            locked = True
            self._store_lock_local.depth = 1
            try:
                yield
            finally:
                self._store_lock_local.depth = 0
        finally:
            if locked:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
            else:
                handle.close()

    @contextmanager
    def _exclusive_model_operation(self):
        with self._install_lock:
            with self._process_store_lock():
                yield

    def _transaction_backup_path(self, item: dict) -> Path:
        target = self._item_path(item)
        return target.with_name(f".{target.name}.previous")

    def _transaction_staging_path(self, item: dict) -> Path:
        target = self._item_path(item)
        return target.with_name(f".{target.name}.staging")

    def _recover_interrupted_transactions(self) -> None:
        for item in get_catalog():
            if item.get("type") != "voice" and item.get("engine") != "marian":
                continue
            target = self._item_path(item)
            backup = self._transaction_backup_path(item)
            staging_marker = target / ".staging_manifest.json"

            if backup.exists():
                if target.exists():
                    # The new validated directory was already committed. Failure
                    # to remove the old backup must not make the active model
                    # unusable or prevent the application from starting. A later
                    # maintenance pass can retry cleanup.
                    try:
                        shutil.rmtree(backup)
                    except OSError:
                        pass
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(backup, target)

            # A crash after the atomic staging->target rename but before marker
            # cleanup leaves a harmless transaction marker in an otherwise fully
            # validated active model. Remove it opportunistically.
            if target.exists():
                try:
                    staging_marker.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _catalog_fingerprint(item: dict) -> str:
        """Stable fingerprint of fields that materially define installed data."""
        keys = (
            "id", "engine", "type", "source_lang", "target_lang", "model_repo",
            "url", "config_url", "filename", "config_filename", "sha256", "pip",
        )
        payload = {key: item.get(key) for key in keys if item.get(key) is not None}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _manifest_path(self, item: dict) -> Path:
        return self._item_path(item) / "install_manifest.json"

    def _read_manifest(self, item: dict) -> dict:
        path = self._manifest_path(item)
        if not path.is_file():
            return {}
        try:
            data = read_json(path)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _write_install_manifest(self, item: dict) -> None:
        path = self._manifest_path(item)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {
            "schema": 1,
            "model_id": item["id"],
            "catalog_fingerprint": self._catalog_fingerprint(item),
            "installed_at": datetime.now(timezone.utc).isoformat(),
        })

    def _catalog_changed(self, item: dict) -> bool:
        manifest = self._read_manifest(item)
        saved = str(manifest.get("catalog_fingerprint") or "")
        return bool(saved and saved != self._catalog_fingerprint(item))

    def maintenance_due(self, interval_days: int | None = None) -> bool:
        interval = max(1, int(interval_days or self.AUTO_MAINTENANCE_DAYS))
        try:
            data = read_json(self._maintenance_state_path)
            checked_at = float(data.get("checked_at_epoch") or 0) if isinstance(data, dict) else 0.0
        except (OSError, ValueError, TypeError):
            checked_at = 0.0
        return (datetime.now(timezone.utc).timestamp() - checked_at) >= interval * 86400

    def _mark_maintenance_checked(self) -> None:
        now = datetime.now(timezone.utc)
        atomic_write_json(self._maintenance_state_path, {
            "schema": 1,
            "checked_at_epoch": now.timestamp(),
            "checked_at": now.isoformat(),
        })

    def startup_scan_installed_updates(self) -> dict:
        """Fast scan every launch; expensive integrity hashing only periodically.

        Catalog/manifest changes are still detected on every startup, so a newer
        application can offer compatible model updates immediately.  Full SHA/JSON
        verification is throttled by ``AUTO_MAINTENANCE_DAYS``.  A discovered
        repair or scan error intentionally does not advance the maintenance clock,
        so the next launch re-checks the broken component instead of hiding it for
        another week.
        """
        # The scan is not read-only: a verified legacy installation may receive
        # an install_manifest and a successful deep scan advances manager_state.
        # Keep the complete decision + write sequence under the same cross-process
        # store lock so a second application cannot update/remove files mid-scan.
        with self._exclusive_model_operation():
            verify_integrity = self.maintenance_due()
            # Keep the public call boundary: tests/extensions may instrument this
            # method, while the re-entrant store lock keeps nested protection safe.
            result = self.scan_installed_updates(verify_integrity=verify_integrity)
            result["scan_mode"] = "full" if verify_integrity else "quick"

            if verify_integrity:
                items = list(result.get("items") or [])
                errors = list(result.get("errors") or [])
                needs_repair = any(row.get("action") == "repair_needed" for row in items)
                if not errors and not needs_repair:
                    try:
                        self._mark_maintenance_checked()
                    except (OSError, TypeError, ValueError) as exc:
                        errors.append({
                            "model_id": "manager_state",
                            "error": f"{type(exc).__name__}: {exc}",
                        })
                        result["errors"] = errors
            return result

    def check_space(self, required_mb: float) -> bool:
        usage = shutil.disk_usage(self.root)
        # Keep an extra 15% margin for temporary/download extraction files.
        required = max(1.0, float(required_mb)) * 1.15 * 1024 * 1024
        return usage.free >= required

    @staticmethod
    def _dir_size_mb(path: Path) -> float:
        """Best-effort UI size; a transient unreadable file must not hide model status."""
        try:
            if not path.exists():
                return 0.0
            if path.is_file():
                return path.stat().st_size / (1024 * 1024)
        except (OSError, TypeError, ValueError):
            return 0.0

        total = 0
        try:
            entries = path.rglob("*")
            for entry in entries:
                try:
                    if entry.is_file():
                        total += entry.stat().st_size
                except (OSError, TypeError, ValueError):
                    continue
        except (OSError, TypeError, ValueError):
            pass
        return total / (1024 * 1024)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _valid_json_file(path: Path) -> bool:
        try:
            if not path.is_file() or path.stat().st_size <= 1:
                return False
            with path.open("r", encoding="utf-8") as stream:
                return isinstance(json.load(stream), dict)
        except (OSError, UnicodeError, ValueError, TypeError):
            return False

    def _prepare_transaction_staging(self, item: dict) -> Path:
        staging = self._transaction_staging_path(item)
        marker = staging / ".staging_manifest.json"
        fingerprint = self._catalog_fingerprint(item)
        if staging.exists():
            try:
                data = read_json(marker)
                same_catalog = isinstance(data, dict) and data.get("catalog_fingerprint") == fingerprint
            except (OSError, ValueError, TypeError):
                same_catalog = False
            if not same_catalog:
                shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        atomic_write_json(marker, {
            "schema": 1,
            "model_id": item["id"],
            "catalog_fingerprint": fingerprint,
        })
        return staging

    def _transactional_directory_install(self, item: dict, build_cb, validate_cb) -> None:
        """Build file-backed model data off to the side, then swap it in.

        Network/build failures intentionally keep the compatible staging directory
        so the next attempt can resume partial downloads. Validation failures clear
        staging because those bytes are known-bad. The active model is renamed only
        after staged data has passed local validation.
        """
        target = self._item_path(item)
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = self._transaction_backup_path(item)

        # Recover a crash that happened in the tiny rename window of an earlier
        # update. The process-store lock guarantees no other writer is active.
        if backup.exists():
            if target.exists():
                shutil.rmtree(backup)
            else:
                os.replace(backup, target)

        staging = self._prepare_transaction_staging(item)
        try:
            build_cb(staging)
        except Exception:
            # Keep partial staged bytes for Range/Hugging Face resume.
            raise

        try:
            validate_cb(staging)
        except Exception:
            # A completed-but-invalid staging tree must never be resumed/adopted.
            shutil.rmtree(staging, ignore_errors=True)
            raise

        moved_previous = False
        try:
            if target.exists():
                os.replace(target, backup)
                moved_previous = True
            try:
                # Keep the staging marker through the atomic rename. If the
                # process dies immediately before/after this operation, the next
                # launch can distinguish resumable staging from unrelated bytes
                # instead of throwing away a fully downloaded model.
                os.replace(staging, target)
            except Exception as commit_exc:
                if moved_previous and backup.exists() and not target.exists():
                    try:
                        os.replace(backup, target)
                        moved_previous = False
                    except Exception as rollback_exc:
                        raise RuntimeError(
                            f"Не удалось завершить обновление {item.get('name', item['id'])} "
                            f"и восстановить предыдущую установку: {rollback_exc}"
                        ) from commit_exc
                raise

            try:
                (target / ".staging_manifest.json").unlink(missing_ok=True)
            except OSError:
                # The marker is harmless and startup recovery removes it later.
                pass

            if backup.exists():
                try:
                    shutil.rmtree(backup)
                except OSError:
                    # Commit already succeeded. Keep a stale rollback copy rather
                    # than reporting a false update failure; startup recovery will
                    # retry cleanup when Windows releases any lingering handles.
                    pass
            moved_previous = False
        finally:
            if moved_previous and backup.exists() and not target.exists():
                try:
                    os.replace(backup, target)
                except OSError:
                    # The original exception already reports a failed commit/rollback.
                    pass

    def _validate_marian_directory(self, path: Path) -> None:
        missing = [
            name for name in self.MARIAN_FILES
            if not (path / name).is_file() or (path / name).stat().st_size <= 0
        ]
        if missing:
            raise RuntimeError(f"MarianMT скачан не полностью; отсутствуют/пусты: {', '.join(missing)}")
        invalid_json = [name for name in self.MARIAN_FILES if name.endswith(".json") and not self._valid_json_file(path / name)]
        if invalid_json:
            raise RuntimeError(
                f"MarianMT: повреждены JSON-файлы конфигурации: {', '.join(invalid_json)}"
            )

    def _validate_piper_voice_directory(self, item: dict, path: Path) -> None:
        model_path = path / item["filename"]
        config_path = path / item["config_filename"]
        if not model_path.is_file() or model_path.stat().st_size <= 0:
            raise RuntimeError("Piper: файл голосовой модели не скачан.")
        expected = str(item.get("sha256") or "")
        if expected and self._sha256(model_path) != expected:
            raise RuntimeError("Piper: SHA256 голосовой модели не совпадает с каталогом.")
        if not self._valid_json_file(config_path):
            raise RuntimeError("Piper: конфигурация голоса повреждена или не является JSON-объектом.")

    def _item_path(self, item: dict) -> Path:
        if item.get("type") == "voice":
            return self.root / "piper" / "voices" / item["id"]
        if item.get("id") == "piper-engine":
            return self.root / "piper" / "engine"
        return self.root / "translation" / item["id"]

    def status(self, model_id: str, *, verify_checksum: bool = False) -> dict:
        item = get_model(model_id)
        path = self._item_path(item)
        installed = False
        integrity = "missing"
        detail = ""

        if item["id"] == "piper-engine":
            module_present = importlib.util.find_spec("piper") is not None
            installed_version = self._installed_distribution_version("piper-tts") if module_present else ""
            installed = bool(module_present and installed_version)
            if installed:
                integrity = "ok"
                detail = f"piper-tts {installed_version}"
            elif module_present:
                integrity = "metadata_missing"
                detail = "Модуль piper найден, но metadata пакета piper-tts недоступна"
            else:
                integrity = "missing"
                detail = "piper-tts не установлен"
        elif item.get("engine") == "argos":
            source = item.get("source_lang", "en")
            target = item.get("target_lang", "ru")
            installed = self._argos_direct_pair_installed(source, target)
            integrity = "ok" if installed else "missing"
            detail = "Прямой пакет Argos" if installed else "прямой языковой пакет не установлен"
        elif item.get("engine") == "marian":
            installed = all(
                (path / name).is_file() and (path / name).stat().st_size > 0
                for name in self.MARIAN_FILES
            )
            invalid_json = []
            if installed and verify_checksum:
                invalid_json = [
                    name for name in self.MARIAN_FILES
                    if name.endswith(".json") and not self._valid_json_file(path / name)
                ]
            if invalid_json:
                integrity = "config_invalid"
                detail = f"Повреждены JSON-файлы: {', '.join(invalid_json)}"
            else:
                integrity = "ok" if installed else ("partial" if path.exists() else "missing")
        elif item.get("type") == "voice":
            model_path = path / item["filename"]
            config_path = path / item["config_filename"]
            installed = (
                model_path.is_file() and model_path.stat().st_size > 0
                and config_path.is_file() and config_path.stat().st_size > 1
            )
            if installed and verify_checksum:
                if item.get("sha256") and self._sha256(model_path) != item["sha256"]:
                    integrity = "checksum_failed"
                elif not self._valid_json_file(config_path):
                    integrity = "config_invalid"
                else:
                    integrity = "ok"
            else:
                integrity = "ok" if installed else ("partial" if path.exists() else "missing")
            if installed and item.get("sha256") and not verify_checksum:
                detail = "Файлы голоса найдены; SHA-256 и JSON-конфиг проверяются кнопкой «Проверить»"

        size = self._dir_size_mb(path)
        if item.get("engine") == "argos" and installed:
            installed_size = self._argos_direct_pair_size_mb(
                item.get("source_lang", "en"), item.get("target_lang", "ru")
            )
            if installed_size > 0:
                size = installed_size
        return asdict(ModelStatus(
            model_id=model_id,
            installed=installed,
            size_mb=round(size, 2),
            expected_mb=float(item.get("size_disk_mb") or 0),
            integrity=integrity,
            path=str(path),
            detail=detail,
        ))

    def get_status(self) -> list[dict]:
        rows = []
        for item in get_catalog():
            row = dict(item)
            row.update(self.status(item["id"]))
            rows.append(row)
        return rows

    def verify(self, model_id: str, expected_mb: float = 0) -> dict:
        return self.status(model_id, verify_checksum=True)

    def install_prepare(self, model_id: str, expected_mb: float = 0) -> Path:
        item = get_model(model_id)
        required_mb = expected_mb or float(item.get("size_disk_mb") or item.get("size_download_mb") or 0)
        if not self.check_space(required_mb):
            raise OSError(f"Недостаточно свободного места для {item['name']} (~{required_mb:.0f} МБ).")
        path = self._item_path(item)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _python_command(self) -> list[str]:
        if not getattr(sys, "frozen", False):
            return [sys.executable]
        raise RuntimeError(
            "Этот EXE не может подмешать новый Python-модуль в уже собранный PyInstaller bundle. "
            "Нужный локальный движок должен быть включён при сборке EXE. "
            "Модели/языковые пакеты после этого можно устанавливать из программы."
        )

    def _pip_install(self, *packages: str, progress_cb=None):
        if progress_cb:
            progress_cb("Установка Python-компонентов…")
        cmd = self._python_command() + ["-m", "pip", "install", "--disable-pip-version-check", *packages]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, creationflags=no_console_creationflags())
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "")[-4000:]
            raise RuntimeError(f"pip завершился с кодом {completed.returncode}: {tail}")

    @staticmethod
    def _exact_pip_version(spec: str) -> str:
        text = str(spec or "")
        return text.split("==", 1)[1].strip() if "==" in text else ""

    @staticmethod
    def _installed_distribution_version(distribution: str) -> str:
        try:
            return str(importlib.metadata.version(distribution) or "")
        except importlib.metadata.PackageNotFoundError:
            return ""

    def _piper_engine_version_state(self, item: dict) -> tuple[str, str, bool]:
        expected = self._exact_pip_version(item.get("pip") or "")
        installed = self._installed_distribution_version("piper-tts")
        mismatch = bool(expected and installed and installed != expected)
        return installed, expected, mismatch

    def _pip_uninstall(self, *packages: str):
        cmd = self._python_command() + ["-m", "pip", "uninstall", "-y", *packages]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=600, creationflags=no_console_creationflags())
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "")[-3000:]
            raise RuntimeError(f"pip uninstall завершился с кодом {completed.returncode}: {tail}")

    def _download(self, url: str, target: Path, expected_sha256: str | None = None, progress_cb=None):
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".part")
        resume_from = temp.stat().st_size if temp.is_file() else 0

        def request_for(offset: int):
            headers = {"User-Agent": "VideoTranslatorPRO/HybridAIv9"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            return urllib.request.Request(url, headers=headers)

        def transfer(response, offset: int, append: bool):
            content_length = int(response.headers.get("Content-Length") or 0)
            total = (offset + content_length) if content_length else 0
            done = offset
            if progress_cb and append:
                progress_cb(f"Продолжаю загрузку с {offset / 1024 / 1024:.0f} МБ…")
            with temp.open("ab" if append else "wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        progress_cb(f"Скачивание: {done / 1024 / 1024:.0f}/{total / 1024 / 1024:.0f} МБ")

        resumed = False
        restart_from_zero = False
        if resume_from:
            try:
                with urllib.request.urlopen(request_for(resume_from), timeout=45) as response:
                    status = int(getattr(response, "status", 0) or getattr(response, "getcode", lambda: 0)() or 0)
                    content_range = str(response.headers.get("Content-Range") or "")
                    resumed = status == 206 and content_range.lower().startswith(f"bytes {resume_from}-")
                    if resumed:
                        transfer(response, resume_from, True)
                    else:
                        restart_from_zero = True
            except urllib.error.HTTPError as exc:
                if exc.code != 416:
                    raise
                # A crash can leave a fully downloaded .part just before the
                # final rename.  When a checksum is known, accept that complete
                # file without downloading it again.  Otherwise restart cleanly.
                if expected_sha256 and temp.is_file() and self._sha256(temp) == expected_sha256:
                    os.replace(temp, target)
                    return target
                restart_from_zero = True

            if restart_from_zero:
                # A 200 response means Range was ignored. A malformed/missing
                # Content-Range on 206 or HTTP 416 is equally unsafe unless the
                # existing partial was checksum-proven complete. Fetch from byte 0.
                with urllib.request.urlopen(request_for(0), timeout=45) as response:
                    transfer(response, 0, False)
        else:
            with urllib.request.urlopen(request_for(0), timeout=45) as response:
                transfer(response, 0, False)

        if expected_sha256 and self._sha256(temp) != expected_sha256:
            temp.unlink(missing_ok=True)
            raise RuntimeError("SHA256 скачанной модели не совпадает с каталогом.")
        os.replace(temp, target)
        return target

    @staticmethod
    def _remote_content_length_mb(url: str) -> float:
        """Best-effort HEAD size for optional language packages."""
        if not url:
            return 0.0
        try:
            request = urllib.request.Request(
                str(url),
                headers={"User-Agent": "VideoTranslatorPRO/HybridAIv9"},
                method="HEAD",
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                length = int(response.headers.get("Content-Length") or 0)
            return max(0.0, length / (1024 * 1024))
        except Exception:
            return 0.0

    @staticmethod
    def _argos_direct_pair_size_mb(source: str, target: str) -> float:
        try:
            import argostranslate.package
            total = 0
            for pkg in argostranslate.package.get_installed_packages():
                if str(pkg.from_code) == source and str(pkg.to_code) == target:
                    path = Path(pkg.package_path)
                    total += sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
            return total / (1024 * 1024)
        except Exception:
            return 0.0

    @staticmethod
    def _argos_pair_installed(source: str, target: str) -> bool:
        """Return whether Argos can translate the pair, including pivot routes."""
        try:
            import argostranslate.translate
            languages = argostranslate.translate.get_installed_languages()
            src = next((lang for lang in languages if lang.code == source), None)
            dst = next((lang for lang in languages if lang.code == target), None)
            if not src or not dst:
                return False
            src.get_translation(dst)
            return True
        except Exception:
            return False

    @staticmethod
    def _argos_direct_pair_installed(source: str, target: str) -> bool:
        """Return whether the exact Argos package is installed, not a pivot route."""
        try:
            import argostranslate.package
            return any(
                str(pkg.from_code) == source and str(pkg.to_code) == target
                for pkg in argostranslate.package.get_installed_packages()
            )
        except Exception:
            return False

    def _install_argos_pair(self, source: str, target: str, item: dict | None = None, progress_cb=None, force: bool = False):
        try:
            import argostranslate.package
        except ImportError:
            self._pip_install("argostranslate", progress_cb=progress_cb)
            import argostranslate.package

        already_installed = (
            self._argos_direct_pair_installed(source, target)
            if item and item.get("url")
            else self._argos_pair_installed(source, target)
        )
        if already_installed and not force:
            return

        if item and item.get("url"):
            path = self.install_prepare(item["id"])
            package_file = path / item.get("filename", f"translate-{source}_{target}.argosmodel")
            self._download(item["url"], package_file, progress_cb=progress_cb)
            if progress_cb:
                progress_cb("Установка языкового пакета Argos…")
            argostranslate.package.install_from_path(package_file)
            # The installed package is extracted into Argos' package directory;
            # keeping the source .argosmodel would nearly duplicate disk usage.
            package_file.unlink(missing_ok=True)
            return

        if progress_cb:
            progress_cb(f"Поиск пакета Argos {source}→{target}…")
        argostranslate.package.update_package_index()
        packages = argostranslate.package.get_available_packages()
        candidates = [p for p in packages if p.from_code == source and p.to_code == target]
        if not candidates:
            raise RuntimeError(f"В каталоге Argos нет прямого пакета {source}→{target}.")
        package = candidates[-1]
        downloaded = package.download()
        argostranslate.package.install_from_path(downloaded)

    @staticmethod
    def _argos_install_plan(source: str, target: str, packages) -> list[tuple[str, str]]:
        """Choose the smallest Argos route, preferring a direct package.

        Argos can compose installed translations through an intermediate
        language.  Its public package index is English-centric, so for a
        missing direct X→RU route the most useful offline plan is X→EN→RU.
        """
        pairs = {(str(p.from_code), str(p.to_code)) for p in packages}
        direct = (source, target)
        if direct in pairs:
            return [direct]
        if source != "en" and target != "en":
            via_english = [(source, "en"), ("en", target)]
            if all(pair in pairs for pair in via_english):
                return via_english
        return []

    def _install_argos_route(self, source: str, target: str, *, progress_cb=None) -> None:
        import argostranslate.package

        if progress_cb:
            progress_cb(f"Поиск локального маршрута Argos {source}→{target}…")
        argostranslate.package.update_package_index()
        packages = list(argostranslate.package.get_available_packages())
        plan = self._argos_install_plan(source, target, packages)
        if not plan:
            raise RuntimeError(
                f"В каталоге Argos нет ни прямого {source}→{target}, "
                f"ни маршрута {source}→en→{target}."
            )
        by_pair = {(str(p.from_code), str(p.to_code)): p for p in packages}
        for pos, pair in enumerate(plan, 1):
            if self._argos_pair_installed(*pair):
                continue
            package = by_pair[pair]
            links = list(getattr(package, "links", None) or [])
            download_mb = self._remote_content_length_mb(links[0]) if links else 0.0
            if download_mb and not self.check_space(max(download_mb * 2.2, download_mb + 64.0)):
                raise OSError(
                    f"Недостаточно свободного места для Argos {pair[0]}→{pair[1]} "
                    f"(загрузка ≈{download_mb:.0f} МБ + распаковка)."
                )
            if progress_cb:
                size_text = f", ≈{download_mb:.0f} МБ" if download_mb else ""
                progress_cb(f"Argos {pair[0]}→{pair[1]}: пакет {pos}/{len(plan)}{size_text}…")
            downloaded = Path(package.download())
            try:
                argostranslate.package.install_from_path(downloaded)
            finally:
                # AvailablePackage.download() caches the archive; after a
                # successful/extracted install it is no longer needed.
                downloaded.unlink(missing_ok=True)

    def ensure_argos_pair(self, source: str, target: str, *, progress_cb=None) -> bool:
        source = str(source or "").lower().split("-")[0]
        target = str(target or "").lower().split("-")[0]
        if not source or not target or source == target:
            return True
        with self._exclusive_model_operation():
            if self._argos_pair_installed(source, target):
                return True
            try:
                import argostranslate.package  # noqa: F401
            except ImportError:
                self._pip_install("argostranslate", progress_cb=progress_cb)
            self._install_argos_route(source, target, progress_cb=progress_cb)
            return self._argos_pair_installed(source, target)

    def _install_marian(self, item: dict, progress_cb=None):
        missing = [
            name for name in ("transformers", "sentencepiece", "huggingface_hub")
            if importlib.util.find_spec(name) is None
        ]
        if missing:
            self._pip_install("transformers>=4,<6", "sentencepiece", "huggingface_hub", progress_cb=progress_cb)
        from huggingface_hub import hf_hub_download

        required_mb = float(item.get("size_disk_mb") or item.get("size_download_mb") or 0)
        if not self.check_space(required_mb):
            raise OSError(f"Недостаточно свободного места для {item['name']} (~{required_mb:.0f} МБ).")

        files = self.MARIAN_FILES

        def build(path: Path):
            # local_dir avoids retaining a second complete model in the global
            # Hugging Face cache.  The local_dir itself is staged transactionally.
            for pos, filename in enumerate(files, 1):
                if progress_cb:
                    progress_cb(f"MarianMT: файл {pos}/{len(files)} — {filename}")
                hf_hub_download(
                    repo_id=item["model_repo"],
                    filename=filename,
                    local_dir=str(path),
                )

        self._transactional_directory_install(
            item, build, lambda path: self._validate_marian_directory(path)
        )

    def _install_piper_engine(self, item: dict, progress_cb=None, force: bool = False):
        module_present = importlib.util.find_spec("piper") is not None
        installed = bool(module_present and self._installed_distribution_version("piper-tts"))
        if force and getattr(sys, "frozen", False):
            raise RuntimeError(
                "Piper Engine встроен в этот EXE. Для обновления движка установите новую версию программы; "
                "голоса и модели при этом останутся в постоянном хранилище."
            )
        if not installed:
            self._pip_install(item.get("pip") or "piper-tts", progress_cb=progress_cb)
        elif force:
            self._pip_install("--upgrade", item.get("pip") or "piper-tts", progress_cb=progress_cb)
        marker = self.install_prepare(item["id"])
        (marker / "installed.txt").write_text("piper-tts installed\n", encoding="utf-8")

    def _install_piper_voice(self, item: dict, progress_cb=None):
        if not self.status("piper-engine").get("installed"):
            if progress_cb:
                progress_cb("Сначала устанавливаю Piper Engine (~34.1 МБ)…")
            self._install_piper_engine(get_model("piper-engine"), progress_cb=progress_cb)

        required_mb = float(item.get("size_disk_mb") or item.get("size_download_mb") or 0)
        if not self.check_space(required_mb):
            raise OSError(f"Недостаточно свободного места для {item['name']} (~{required_mb:.0f} МБ).")

        def build(path: Path):
            model_path = path / item["filename"]
            config_path = path / item["config_filename"]
            expected = str(item.get("sha256") or "")
            model_ready = (
                model_path.is_file() and model_path.stat().st_size > 0
                and (not expected or self._sha256(model_path) == expected)
            )
            if not model_ready:
                self._download(item["url"], model_path, item.get("sha256"), progress_cb)
            elif progress_cb:
                progress_cb("Piper: модель уже скачана, продолжаю с конфигурации…")
            if not self._valid_json_file(config_path):
                self._download(item["config_url"], config_path, progress_cb=progress_cb)

        self._transactional_directory_install(
            item, build, lambda path: self._validate_piper_voice_directory(item, path)
        )

    def install(self, model_id: str, *, progress_cb=None, force: bool = False) -> dict:
        with self._exclusive_model_operation():
            item = get_model(model_id)
            current = self.status(model_id)
            if current["installed"] and not force:
                if item.get("type") == "voice":
                    current = self.verify(model_id)
                    if current.get("integrity") != "ok":
                        force = True
                if not force:
                    if not self._read_manifest(item):
                        self._write_install_manifest(item)
                    return current
            if item["id"] == "piper-engine":
                self._install_piper_engine(item, progress_cb, force=force)
            elif item.get("engine") == "argos":
                self._install_argos_pair(
                    item["source_lang"], item["target_lang"], item=item, progress_cb=progress_cb, force=force
                )
            elif item.get("engine") == "marian":
                self._install_marian(item, progress_cb)
            elif item.get("type") == "voice":
                self._install_piper_voice(item, progress_cb)
            else:
                raise RuntimeError(f"Неизвестный тип модели: {item}")
            status = self.verify(model_id)
            if not status["installed"] or status["integrity"] not in {"ok"}:
                raise RuntimeError(f"Проверка после установки не пройдена: {status['integrity']}")
            self._write_install_manifest(item)
            return status

    def update(self, model_id: str, *, progress_cb=None) -> dict:
        return self.install(model_id, progress_cb=progress_cb, force=True)

    def scan_installed_updates(self, *, verify_integrity: bool = True) -> dict:
        """Inspect installed managed components without downloading or upgrading them.

        The application catalog is the compatibility allow-list. A newer app may
        change a catalog fingerprint/version and then this scan offers the user an
        update. It never follows arbitrary upstream "latest" versions.

        A scan may adopt a verified legacy installation by writing a manifest, so
        it participates in the same cross-process store transaction as installers.
        """
        with self._exclusive_model_operation():
            return self._scan_installed_updates_unlocked(verify_integrity=verify_integrity)

    def _scan_installed_updates_unlocked(self, *, verify_integrity: bool = True) -> dict:
        items = []
        errors = []
        for item in get_catalog():
            try:
                status = self.status(item["id"], verify_checksum=bool(verify_integrity))
                manifest = self._read_manifest(item)
                if not status["installed"] and not manifest:
                    continue

                action = "healthy"
                reason = ""
                can_update = True
                if not status["installed"] or status.get("integrity") not in {"ok"}:
                    if item.get("id") == "piper-engine" and getattr(sys, "frozen", False):
                        action = "app_update_required"
                        can_update = False
                        reason = "встроенный Piper Engine отсутствует или повреждён в сборке EXE"
                    else:
                        action = "repair_needed"
                        reason = status.get("integrity") or "incomplete"
                elif item.get("id") == "piper-engine":
                    installed_ver, expected_ver, mismatch = self._piper_engine_version_state(item)
                    if mismatch or (manifest and self._catalog_changed(item)):
                        if getattr(sys, "frozen", False):
                            action = "app_update_required"
                            can_update = False
                        else:
                            action = "update_available"
                        reason = (
                            f"Piper Engine {installed_ver or '?'} → {expected_ver or 'версия из каталога'}"
                            if mismatch else "изменена поддерживаемая версия движка"
                        )
                elif manifest and self._catalog_changed(item):
                    action = "update_available"
                    reason = "изменена поддерживаемая версия/источник в каталоге приложения"

                if action == "healthy" and not manifest:
                    # A legacy install has no trusted baseline yet. Even during a
                    # lightweight startup scan, do one full local verification
                    # before adopting it. This is local I/O only and prevents a
                    # pre-existing corrupt voice from receiving a healthy manifest.
                    if not verify_integrity:
                        verified = self.status(item["id"], verify_checksum=True)
                        status = verified
                        if not verified.get("installed") or verified.get("integrity") != "ok":
                            action = "repair_needed"
                            reason = verified.get("integrity") or "incomplete"
                    if action == "healthy":
                        self._write_install_manifest(item)

                items.append({
                    "model_id": item["id"],
                    "name": item.get("name", item["id"]),
                    "action": action,
                    "reason": reason,
                    "can_update": can_update,
                    "size_download_mb": float(item.get("size_download_mb") or 0),
                    "integrity": status.get("integrity", "unknown"),
                })
            except Exception as exc:
                errors.append({
                    "model_id": item.get("id", "unknown"),
                    "error": f"{type(exc).__name__}: {exc}",
                })
        return {"checked": True, "items": items, "errors": errors}

    def update_if_needed(self, model_id: str, *, progress_cb=None) -> dict:
        """Keep an already-installed catalog item healthy without redownloading it blindly.

        A voice is checksum-verified.  Other managed model data is refreshed only
        when a manifest from an earlier application/catalog version no longer
        matches the current catalog definition. Existing installs created before
        manifests were introduced are adopted after a successful local verification.
        """
        with self._install_lock:
            item = get_model(model_id)
            current = self.status(model_id)
            manifest = self._read_manifest(item)
            if not current["installed"]:
                if not manifest:
                    return {**current, "maintenance_action": "not_installed"}
                if progress_cb:
                    progress_cb(f"{item['name']}: ранее установленный компонент неполон, восстанавливаю…")
                repaired = self.install(model_id, progress_cb=progress_cb, force=True)
                return {**repaired, "maintenance_action": "repaired"}

            # Piper Engine is executable code. Source installs can update the
            # pinned compatible package. Frozen builds must receive engine-code
            # updates with a new application build.
            if item.get("id") == "piper-engine":
                _installed_ver, _expected_ver, version_mismatch = self._piper_engine_version_state(item)
                needs_update = version_mismatch or bool(manifest and self._catalog_changed(item))
                if needs_update:
                    if getattr(sys, "frozen", False):
                        return {**current, "maintenance_action": "app_update_required"}
                    if progress_cb:
                        progress_cb(f"{item['name']}: обновляю совместимую версию движка…")
                    refreshed = self.install(model_id, progress_cb=progress_cb, force=True)
                    return {**refreshed, "maintenance_action": "updated"}
                if not manifest:
                    self._write_install_manifest(item)
                return {**current, "maintenance_action": "healthy"}

            if item.get("type") == "voice":
                verified = self.verify(model_id)
                if verified.get("integrity") != "ok":
                    if progress_cb:
                        progress_cb(f"{item['name']}: файл повреждён, восстанавливаю…")
                    repaired = self.install(model_id, progress_cb=progress_cb, force=True)
                    return {**repaired, "maintenance_action": "repaired"}
                current = verified

            if manifest and self._catalog_changed(item):
                if progress_cb:
                    progress_cb(f"{item['name']}: обнаружена новая версия каталога, обновляю…")
                refreshed = self.install(model_id, progress_cb=progress_cb, force=True)
                return {**refreshed, "maintenance_action": "updated"}

            if not manifest:
                # Adopt an installation created by an older application version.
                # Do not redownload hundreds of MB just because metadata was not
                # written by that older version.
                self._write_install_manifest(item)
            return {**current, "maintenance_action": "healthy"}

    def maintain_installed(self, *, progress_cb=None, interval_days: int | None = None, force: bool = False) -> dict:
        """Periodically verify/update only items the user already installed."""
        if not force and not self.maintenance_due(interval_days):
            return {"checked": False, "reason": "not_due", "items": []}

        results = []
        errors = []
        for item in get_catalog():
            try:
                status = self.status(item["id"])
                if not status["installed"] and not self._read_manifest(item):
                    continue
                if progress_cb:
                    progress_cb(f"Проверяю установленное: {item['name']}…")
                result = self.update_if_needed(item["id"], progress_cb=progress_cb)
                results.append({
                    "model_id": item["id"],
                    "action": result.get("maintenance_action", "healthy"),
                    "integrity": result.get("integrity", "unknown"),
                })
            except Exception as exc:
                errors.append({
                    "model_id": item["id"],
                    "error": f"{type(exc).__name__}: {exc}",
                })

        # A failed check is still a completed maintenance attempt; otherwise an
        # offline machine would retry network work on every application launch.
        self._mark_maintenance_checked()
        return {"checked": True, "items": results, "errors": errors}

    def remove(self, model_id: str) -> bool:
        with self._exclusive_model_operation():
            item = get_model(model_id)
            removed = False
            if item.get("id") == "piper-engine" and importlib.util.find_spec("piper") is not None:
                if getattr(sys, "frozen", False):
                    raise RuntimeError("Piper встроен в этот EXE и удаляется только пересборкой программы.")
                self._pip_uninstall("piper-tts")
                removed = True
            if item.get("engine") == "argos":
                try:
                    import argostranslate.package
                except ImportError:
                    argostranslate = None
                if argostranslate is not None:
                    for pkg in list(argostranslate.package.get_installed_packages()):
                        if pkg.from_code == item.get("source_lang") and pkg.to_code == item.get("target_lang"):
                            argostranslate.package.uninstall(pkg)
                            removed = True
            path = self._item_path(item)
            if path.exists():
                shutil.rmtree(path)
                if path.exists():
                    raise OSError(f"Не удалось удалить локальные данные модели: {path}")
                removed = True
            if item.get("type") == "voice" or item.get("engine") == "marian":
                for transient in (self._transaction_staging_path(item), self._transaction_backup_path(item)):
                    if transient.exists():
                        shutil.rmtree(transient)
                        removed = True
            return removed

    def installed_piper_voices(self, preferred_id: str | None = None,
                               language: str = "ru") -> list[tuple[str, Path]]:
        """Return all usable installed Piper voices in fallback order.

        The preferred voice is returned first when it is installed.  Keeping the
        whole ordered list lets the runtime continue with another local voice when
        one model is corrupt or the Piper engine rejects that specific model.
        """
        candidates = get_catalog()
        if preferred_id:
            candidates.sort(key=lambda row: row.get("id") != preferred_id)
        installed: list[tuple[str, Path]] = []
        for item in candidates:
            if item.get("type") != "voice" or item.get("language") != language:
                continue
            path = self._item_path(item)
            model_path = path / item["filename"]
            config_path = path / item["config_filename"]
            # Hot TTS path: do not recursively size the directory or hash a
            # ~63 MB ONNX file for every phrase. Explicit Verify does SHA-256.
            if (
                model_path.is_file() and model_path.stat().st_size > 0
                and self._valid_json_file(config_path)
            ):
                installed.append((str(item["id"]), model_path))
        return installed

    def resolve_piper_voice(self, preferred_id: str | None = None, language: str = "ru") -> tuple[str, Path] | None:
        """Return the first usable installed voice id/path selected for local TTS.

        The actual id matters for cache correctness: if the preferred voice is
        absent and another installed voice is used as fallback, audio generated
        by that fallback must never be cached under the preferred voice id.
        """
        voices = self.installed_piper_voices(preferred_id, language)
        return voices[0] if voices else None

    def preferred_piper_voice_path(self, preferred_id: str | None = None, language: str = "ru") -> Path | None:
        resolved = self.resolve_piper_voice(preferred_id, language)
        return resolved[1] if resolved else None
