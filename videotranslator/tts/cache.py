"""Owner module for: TTSCache."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import hashlib
import json
import os
import re
import shutil
import threading
import time
from videotranslator.config import TTS_CACHE_MAX_BYTES, TTS_CACHE_ORPHAN_RETENTION_HOURS, TTS_CACHE_PREPARED_RETENTION_HOURS, TTS_CACHE_RECOVERY_RETENTION_DAYS, TTS_CACHE_SCHEMA_VERSION, TTS_CACHE_SIZE_CHECK_EVERY, TTS_CACHE_TARGET_BYTES
from videotranslator.core.diagnostics import compact_exception, diagnostic_json_value
from videotranslator.core.paths import get_tts_cache_dir
from videotranslator.media.audio import ffmpeg_audio_ok
from videotranslator.sync.pause import normalize_tts_text


class TTSCache:
    """Постоянный потокобезопасный кэш TTS по тексту, голосу, скорости и провайдеру."""

    _maintenance_lock = threading.Lock()

    def __init__(self, directory: Path | None = None, max_bytes: int = TTS_CACHE_MAX_BYTES):
        self.directory = Path(directory or get_tts_cache_dir())
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max(0, int(max_bytes))
        self._locks_guard = threading.Lock()
        self._locks = {}
        self._used_entries_lock = threading.Lock()
        self._used_entries = set()
        self._prepared_size_lock = threading.Lock()
        self._prepared_stores_since_size_check = max(0, TTS_CACHE_SIZE_CHECK_EVERY - 1)
        self._prepared_cache_full = False

    @staticmethod
    def make_key(text: str, voice: str, rate_pct: int, provider: str, language: str,
                 extra: dict | None = None) -> str:
        payload = {
            "schema": TTS_CACHE_SCHEMA_VERSION,
            "provider": provider,
            "language": language,
            "voice": voice,
            "rate_pct": int(rate_pct),
            "text": normalize_tts_text(text),
            "extra": diagnostic_json_value(extra or {}),
        }
        packed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(packed.encode("utf-8", errors="replace")).hexdigest()

    def key_lock(self, key: str):
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _audio_path(self, key: str, suffix: str = ".mp3") -> Path:
        return self.directory / f"{key}{suffix}"

    def _mark_used(self, key: str, suffix: str):
        with self._used_entries_lock:
            self._used_entries.add((str(key), str(suffix)))

    def _touch_entry(self, key: str, suffix: str):
        for path in (self._audio_path(key, suffix=suffix), self.directory / f"{key}.json"):
            try:
                if path.exists():
                    os.utime(path, None)
            except OSError:
                pass

    @staticmethod
    def _safe_unlink(path: Path) -> tuple[int, str]:
        try:
            size = path.stat().st_size if path.exists() else 0
            if path.exists():
                path.unlink()
            return int(size), ""
        except (OSError, TypeError, ValueError) as exc:
            return 0, f"{path.name}: {compact_exception(exc, max_len=500)}"

    def _directory_size(self) -> int:
        total = 0
        try:
            for path in self.directory.iterdir():
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
        except OSError:
            return 0
        return int(total)

    def _prepared_cache_has_room(self, incoming_bytes: int) -> bool:
        """Редко проверяет размер папки и не даёт производным WAV перейти жёсткий предел."""
        if not self.max_bytes:
            return True
        with self._prepared_size_lock:
            if self._prepared_cache_full:
                return False
            self._prepared_stores_since_size_check += 1
            if self._prepared_stores_since_size_check < TTS_CACHE_SIZE_CHECK_EVERY:
                return True
            self._prepared_stores_since_size_check = 0

        has_room = self._directory_size() + max(0, int(incoming_bytes)) <= self.max_bytes
        if not has_room:
            with self._prepared_size_lock:
                self._prepared_cache_full = True
        return has_room

    def restore(self, key: str, destination: str, suffix: str = ".mp3") -> bool:
        source = self._audio_path(key, suffix=suffix)
        try:
            if not source.exists() or source.stat().st_size <= 200:
                return False
            shutil.copyfile(source, destination)
            restored = ffmpeg_audio_ok(destination)
            if restored:
                self._mark_used(key, suffix)
                self._touch_entry(key, suffix)
            return restored
        except OSError:
            return False

    def store(self, key: str, source: str, metadata: dict, suffix: str = ".mp3"):
        if not ffmpeg_audio_ok(source):
            return False
        source_size = os.path.getsize(source)
        if suffix.lower() == ".wav" and not self._prepared_cache_has_room(source_size):
            return False
        audio_path = self._audio_path(key, suffix=suffix)
        meta_path = self.directory / f"{key}.json"
        audio_temp = self.directory / f".{key}.{os.getpid()}.{threading.get_ident()}{suffix}.tmp"
        meta_temp = self.directory / f".{key}.{os.getpid()}.{threading.get_ident()}.json.tmp"
        stored = False
        try:
            shutil.copyfile(source, audio_temp)
            os.replace(audio_temp, audio_path)
            data = {
                "schema_version": TTS_CACHE_SCHEMA_VERSION,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                **diagnostic_json_value(metadata),
            }
            with open(meta_temp, "w", encoding="utf-8", newline="\n") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(meta_temp, meta_path)
            stored = True
        finally:
            for path in (audio_temp, meta_temp):
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    pass
        if stored:
            self._mark_used(key, suffix)
        return stored

    def release_used_entries(self, suffixes: set[str] | None = None) -> dict:
        """
        Удаляет только те записи, которые использовал текущий экземпляр переводчика.

        Метод вызывается после успешной проверки итогового MP4. При ошибке или
        отмене можно удалить только производные WAV, сохранив компактные MP3 для
        продолжения после перезапуска.
        """
        selected_suffixes = None if suffixes is None else {str(suffix).lower() for suffix in suffixes}
        with self._used_entries_lock:
            entries = sorted(
                entry for entry in self._used_entries
                if selected_suffixes is None or entry[1].lower() in selected_suffixes
            )

        deleted_entries = 0
        deleted_files = 0
        freed_bytes = 0
        errors = []
        released = set()
        for key, suffix in entries:
            entry_errors = []
            with self.key_lock(key):
                for path in (self._audio_path(key, suffix=suffix), self.directory / f"{key}.json"):
                    existed = path.exists()
                    size, error = self._safe_unlink(path)
                    if existed and not error:
                        deleted_files += 1
                        freed_bytes += size
                    if error:
                        entry_errors.append(error)
            if not entry_errors:
                deleted_entries += 1
                released.add((key, suffix))
            else:
                errors.extend(entry_errors)

        if released:
            with self._used_entries_lock:
                self._used_entries.difference_update(released)

        return {
            "deleted_entries": deleted_entries,
            "deleted_files": deleted_files,
            "freed_bytes": int(freed_bytes),
            "remaining_bytes": self._directory_size(),
            "errors": errors[:20],
        }

    def cleanup_stale_entries(self, retention_days: int = TTS_CACHE_RECOVERY_RETENTION_DAYS,
                              orphan_hours: int = TTS_CACHE_ORPHAN_RETENTION_HOURS,
                              max_bytes: int = TTS_CACHE_MAX_BYTES,
                              target_bytes: int = TTS_CACHE_TARGET_BYTES,
                              prepared_retention_hours: int = TTS_CACHE_PREPARED_RETENTION_HOURS) -> dict:
        """Чистит производные WAV, старые recovery-записи, сироты и забытые *.tmp."""
        now = time.time()
        retention_sec = max(1, int(retention_days)) * 86400
        prepared_retention_sec = max(1, int(prepared_retention_hours)) * 3600
        orphan_sec = max(1, int(orphan_hours)) * 3600
        max_bytes = max(0, int(max_bytes))
        target_bytes = max(0, min(int(target_bytes), max_bytes)) if max_bytes else 0
        result = {
            "deleted_entries": 0,
            "deleted_files": 0,
            "freed_bytes": 0,
            "orphan_entries": 0,
            "prepared_entries": 0,
            "stale_entries": 0,
            "size_limited_entries": 0,
            "errors": [],
        }

        with self._maintenance_lock:
            try:
                files = [path for path in self.directory.iterdir() if path.is_file()]
            except (OSError, TypeError, ValueError) as exc:
                result["errors"].append(compact_exception(exc, max_len=500))
                result["remaining_bytes"] = 0
                return result

            groups = {}
            for path in files:
                if (
                    path.name.startswith(".")
                    and path.name.endswith(".tmp")
                    and re.match(r"^\.[0-9a-f]{64}\.", path.name)
                ):
                    try:
                        age = now - path.stat().st_mtime
                    except OSError:
                        age = 0
                    if age >= orphan_sec:
                        size, error = self._safe_unlink(path)
                        if error:
                            result["errors"].append(error)
                        else:
                            result["deleted_files"] += 1
                            result["freed_bytes"] += size
                    continue
                if path.suffix.lower() not in {".mp3", ".wav", ".json"}:
                    continue
                if not re.fullmatch(r"[0-9a-f]{64}", path.stem):
                    continue
                groups.setdefault(path.stem, []).append(path)

            with self._used_entries_lock:
                active_keys = {key for key, _suffix in self._used_entries}

            candidates = []
            deleted_keys = set()

            def delete_group(key: str, paths: list[Path], reason: str):
                if key in active_keys or key in deleted_keys:
                    return
                group_deleted = False
                for path in paths:
                    existed = path.exists()
                    size, error = self._safe_unlink(path)
                    if error:
                        result["errors"].append(error)
                    elif existed:
                        group_deleted = True
                        result["deleted_files"] += 1
                        result["freed_bytes"] += size
                if group_deleted:
                    deleted_keys.add(key)
                    result["deleted_entries"] += 1
                    result[f"{reason}_entries"] += 1

            for key, paths in groups.items():
                audio = [path for path in paths if path.suffix.lower() in {".mp3", ".wav"}]
                metadata = [path for path in paths if path.suffix.lower() == ".json"]
                try:
                    last_used = max(path.stat().st_mtime for path in paths)
                except (OSError, ValueError):
                    last_used = now
                age = max(0.0, now - last_used)
                if not audio or not metadata:
                    if age >= orphan_sec:
                        delete_group(key, paths, "orphan")
                    continue
                if age >= retention_sec:
                    delete_group(key, paths, "stale")
                    continue

                is_prepared = False
                for metadata_path in metadata:
                    try:
                        with open(metadata_path, "r", encoding="utf-8") as file:
                            provider = str((json.load(file) or {}).get("provider") or "")
                        if provider.startswith("prepared_wav_"):
                            is_prepared = True
                            break
                    except (OSError, ValueError, TypeError, AttributeError):
                        continue

                if is_prepared and age >= prepared_retention_sec:
                    delete_group(key, paths, "prepared")
                    continue
                size = 0
                for path in paths:
                    try:
                        size += path.stat().st_size
                    except OSError:
                        pass
                # При нехватке места сначала удаляем тяжёлые производные WAV.
                # Сжатые MP3 полезнее: по ним озвучку можно восстановить без сети.
                candidates.append((0 if is_prepared else 1, last_used, key, paths, size))

            remaining_bytes = self._directory_size()
            if max_bytes and remaining_bytes > max_bytes:
                for _priority, _last_used, key, paths, size in sorted(candidates):
                    if remaining_bytes <= target_bytes:
                        break
                    before = result["freed_bytes"]
                    delete_group(key, paths, "size_limited")
                    remaining_bytes -= max(0, result["freed_bytes"] - before)

            result["remaining_bytes"] = self._directory_size()
            result["errors"] = result["errors"][:20]
            return result
