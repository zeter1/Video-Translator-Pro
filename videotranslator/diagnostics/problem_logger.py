"""ProblemLogger lifecycle, event writes, and composition root."""

from __future__ import annotations

from videotranslator.core.compat_bridge import call_legacy_override

from pathlib import Path
from datetime import datetime
import json
import os
import platform
import sys
import threading
import traceback
from videotranslator.config import APP_DIAGNOSTIC_VERSION, PROBLEM_LOG_MAX_FILES, PROBLEM_LOG_RETENTION_DAYS, PROBLEM_LOG_SCHEMA_VERSION, PROBLEM_REPEAT_SAMPLE_EVENTS, PROBLEM_SESSION_STALE_SEC
from videotranslator.core.diagnostics import classify_exception, compact_exception, diagnostic_json_value, exception_chain, redact_diagnostic_text, safe_log_filename, should_store_problem_occurrence
from videotranslator.core.io import atomic_write_json
from videotranslator.core.paths import get_problem_logs_dir as _legacy_get_problem_logs_dir, get_program_dir
from videotranslator.diagnostics.files import archive_legacy_problem_logs, cleanup_old_problem_logs, find_previous_problem_summary, installed_package_versions, write_problem_logs_guide
from videotranslator.diagnostics.problem_analysis import ProblemAnalysisMixin
from videotranslator.diagnostics.problem_reporting import ProblemReportingMixin


def get_problem_logs_dir(*args, **kwargs):
    return call_legacy_override('get_problem_logs_dir', _legacy_get_problem_logs_dir, *args, **kwargs)


class ProblemLogger(ProblemAnalysisMixin, ProblemReportingMixin):
    """Structured problem logger composed from focused mixins."""

    @staticmethod
    def _recover_previous_summary(
        previous: dict,
        summary_path: Path | None,
        moved_paths: dict[str, Path] | None = None,
    ) -> dict:
        if not previous:
            return {}
        previous = dict(previous)
        moved_paths = moved_paths or {}
        old_problem_log = str(previous.get("problem_log") or previous.get("events_log") or "")
        if old_problem_log:
            try:
                moved_problem_log = moved_paths.get(str(Path(old_problem_log).resolve()))
            except OSError:
                moved_problem_log = None
            if moved_problem_log:
                previous["problem_log"] = str(moved_problem_log)
                previous["events_log"] = str(moved_problem_log)
        previous_status = str(previous.get("session_status") or previous.get("status") or "")
        if previous_status != "running":
            return {}

        updated_at = str(previous.get("updated_at") or "")
        age_sec = None
        try:
            age_sec = max(0, int((datetime.now().astimezone() - datetime.fromisoformat(updated_at)).total_seconds()))
        except (TypeError, ValueError):
            pass
        stale = age_sec is None or age_sec >= PROBLEM_SESSION_STALE_SEC
        session_id = safe_log_filename(str(previous.get("session_id") or "unknown"), max_len=96)
        recovered = {
            "session_id": previous.get("session_id") or "",
            "original_status": previous_status,
            "status": "interrupted" if stale else "possibly_active",
            "updated_at": updated_at,
            "detected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "age_sec": age_sec,
            "current_stage": previous.get("current_stage") or "",
            "current_input_path": previous.get("current_input_path") or "",
            "problem_log": previous.get("problem_log") or previous.get("events_log") or "",
            "summary": str(summary_path) if summary_path else "",
        }
        if stale and summary_path and summary_path.exists():
            archived = dict(previous)
            archived["status"] = recovered["status"]
            archived["session_status"] = recovered["status"]
            archived["interruption_detection"] = recovered
            try:
                atomic_write_json(summary_path, archived)
            except OSError:
                pass
        return recovered


    def __init__(self):
        self.lock = threading.Lock()
        self.sequence = 0
        self.session_id = f"{datetime.now():%Y%m%d_%H%M%S_%f}_{os.getpid()}"
        self.root_dir = get_problem_logs_dir()
        previous_summary, previous_summary_path = find_previous_problem_summary(self.root_dir)
        moved_paths = archive_legacy_problem_logs(self.root_dir)
        if previous_summary_path:
            try:
                previous_summary_path = moved_paths.get(
                    str(previous_summary_path.resolve()), previous_summary_path
                )
            except OSError:
                pass
        try:
            write_problem_logs_guide(self.root_dir)
        except OSError:
            pass
        cleanup_old_problem_logs(self.root_dir)

        self.session_dir = self.root_dir / f"session_{self.session_id}"
        self.session_dir.mkdir(parents=True, exist_ok=False)
        self.path = self.session_dir / "events.jsonl"
        self.problem_path = self.session_dir / "problems.jsonl"
        self.summary_path = self.session_dir / "summary.json"
        self.report_path = self.session_dir / "report.md"
        self.manifest_path = self.session_dir / "manifest.json"
        self.latest_index_path = self.root_dir / "latest_session.json"
        previous_session = self._recover_previous_summary(
            previous_summary, previous_summary_path, moved_paths
        )
        self._last_summary_write = 0.0
        self._last_report_write = 0.0
        self._occurrence_counts = {}
        self._problem_signatures_seen = set()
        self._stored_event_counts = {"timeline": 0, "problems": 0}
        self._summary = {
            "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "artifact_type": "video_translator_problem_summary",
            "updated_at": "",
            "session_id": self.session_id,
            "session_dir": self.session_dir.name,
            "status": "running",
            "session_status": "running",
            "stale_after_sec": PROBLEM_SESSION_STALE_SEC,
            "previous_session": previous_session,
            "current_stage": "startup",
            "current_input_path": "",
            "current_file": {
                "status": "idle",
                "stage": "startup",
                "input_path": "",
                "output_path": "",
            },
            "batch": {
                "status": "idle",
                "total": 0,
                "started": 0,
                "successful": 0,
                "failed": 0,
                "pending": 0,
                "current_index": 0,
                "files": [],
            },
            "last_event": {},
            "selected_video_encoder": {},
            "tts_voice": {
                "status": "idle",
                "selected_voice": "",
                "fallback_provider": "",
                "voice_may_differ": False,
            },
            "tts_cache": {},
            "network": {
                "incidents_started": 0,
                "incidents_finished": 0,
                "active_services": {},
            },
            "counters": {"info": 0, "warning": 0, "error": 0},
            "event_counts_by_event": {},
            "problem_counts_by_event": {},
            "problem_counts_by_category": {},
            "recent_problems": [],
            "problem_log": "events.jsonl",
            "events_log": "events.jsonl",
            "problems_log": "problems.jsonl",
            "artifacts": {
                "manifest": "manifest.json",
                "report": "report.md",
                "summary": "summary.json",
                "problems": "problems.jsonl",
                "events": "events.jsonl",
            },
            "diagnostics_quality": {
                "events_total": 0,
                "records_stored_total": 0,
                "timeline_events_stored": 0,
                "problem_events_total": 0,
                "problem_events_stored": 0,
                "unique_problem_signatures_total": 0,
                "problem_signatures_outside_recent_window": 0,
                "problem_timeline_duplicates_avoided": 0,
                "repeat_events_compacted": 0,
                "estimated_bytes_saved": 0,
                "problems_with_stage": 0,
                "problems_with_input_path": 0,
                "error_events_total": 0,
                "errors_without_traceback": 0,
                "exceptions_with_traceback": 0,
                "problems_missing_stage_and_input": 0,
                "artifact_write_errors": [],
            },
            "codex_analysis": {},
        }
        self.file = open(self.path, "a", encoding="utf-8", buffering=1)
        try:
            self.problem_file = open(self.problem_path, "a", encoding="utf-8", buffering=1)
        except OSError as exc:
            self.problem_file = None
            self._summary["diagnostics_quality"]["artifact_write_errors"].append({
                "artifact": "problems.jsonl",
                "error": compact_exception(exc),
            })
        try:
            self._write_manifest_locked()
        except Exception as exc:
            self._record_artifact_error_locked("manifest.json", exc)
        cleanup_old_problem_logs(self.root_dir)
        self.event(
            "app_session_started",
            message="Программа запущена; диагностический журнал готов.",
            app_version=APP_DIAGNOSTIC_VERSION,
            python={
                "version": sys.version,
                "executable": sys.executable,
                "frozen": bool(getattr(sys, "frozen", False)),
            },
            system={
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor(),
            },
            paths={
                "program_dir": str(get_program_dir()),
                "working_dir": str(Path.cwd()),
                "session_dir": str(self.session_dir),
                "events_log": str(self.path),
                "problems_log": str(self.problem_path),
                "problem_summary": str(self.summary_path),
                "problem_report": str(self.report_path),
                "manifest": str(self.manifest_path),
            },
            packages=installed_package_versions(),
            retention={"days": PROBLEM_LOG_RETENTION_DAYS, "max_sessions": PROBLEM_LOG_MAX_FILES},
        )
        if previous_session:
            stale = previous_session.get("status") == "interrupted"
            self.event(
                "previous_session_interrupted" if stale else "previous_session_was_running",
                level="warning",
                message=(
                    "Предыдущая сессия давно не обновлялась и помечена как прерванная."
                    if stale else
                    "Обнаружена другая недавно активная сессия; её сводка сохранена отдельно."
                ),
                previous_session=previous_session,
            )


    def event(self, event: str, level: str = "info", message: str = "", **details):
        with self.lock:
            if not hasattr(self, "file") or self.file.closed:
                return
            self.sequence += 1
            safe_details = diagnostic_json_value(details)
            level = str(level)
            event = str(event)
            family = self._problem_family(event, safe_details)
            safe_message = redact_diagnostic_text(message, max_len=6000)
            signature = self._event_signature(event, level, family, safe_message, safe_details)
            occurrence = int(self._occurrence_counts.get(signature, 0)) + 1
            self._occurrence_counts[signature] = occurrence
            compact_repeat = event in PROBLEM_REPEAT_SAMPLE_EVENTS and occurrence > 1
            store_record = not compact_repeat or should_store_problem_occurrence(occurrence)
            record = {
                "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "sequence": self.sequence,
                "level": level,
                "event": event,
                "message": safe_message,
                "thread": threading.current_thread().name,
                "diagnostic": {
                    "is_problem": level in {"warning", "error"},
                    "family": family,
                    "actionability": "investigate" if level == "error" else ("review" if level == "warning" else "context"),
                    "signature": signature,
                    "occurrence": occurrence,
                    "stored_occurrence": occurrence if store_record else 0,
                },
                "details": safe_details,
            }
            self._update_summary_locked(record)
            quality = self._summary["diagnostics_quality"]
            if store_record:
                stored_record = self._compact_repeat_record(record) if compact_repeat else record
                line = json.dumps(stored_record, ensure_ascii=False, separators=(",", ":")) + "\n"
                if level in {"warning", "error"} and self.problem_file is not None:
                    try:
                        self.problem_file.write(line)
                        self.problem_file.flush()
                        self._stored_event_counts["problems"] += 1
                        quality["problem_events_stored"] = self._stored_event_counts["problems"]
                        self._sync_stored_sample_locked(record)
                    except OSError as exc:
                        self._record_artifact_error_locked("problems.jsonl", exc)
                    quality["problem_timeline_duplicates_avoided"] = int(
                        quality.get("problem_timeline_duplicates_avoided") or 0
                    ) + 1
                else:
                    self.file.write(line)
                    self.file.flush()
                    self._stored_event_counts["timeline"] += 1
                    quality["timeline_events_stored"] = self._stored_event_counts["timeline"]
                quality["records_stored_total"] = (
                    self._stored_event_counts["timeline"] + self._stored_event_counts["problems"]
                )
            else:
                estimated_line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                quality["repeat_events_compacted"] = int(quality.get("repeat_events_compacted") or 0) + 1
                quality["estimated_bytes_saved"] = int(quality.get("estimated_bytes_saved") or 0) + len(
                    estimated_line.encode("utf-8", errors="replace")
                )
            force_summary = (
                str(level) == "error"
                or (str(level) == "warning" and store_record)
                or str(event) in {
                    "app_session_started", "app_session_finished", "batch_started", "batch_finished",
                    "batch_file_started", "batch_file_finished", "app_close_requested",
                    "file_pipeline_started", "file_pipeline_finished", "file_pipeline_failed",
                     "file_pipeline_cancelled", "stage_started", "stage_finished",
                     "network_storm_started", "network_storm_finished", "tts_summary",
                     "video_encoder_selected", "tts_voice_preservation_started",
                     "tts_voice_preservation_recovered", "tts_gtts_fallback_enabled",
                     "tts_cache_maintenance_finished", "tts_cache_released_after_success",
                     "tts_prepared_cache_released_after_stop",
                 }
            )
            try:
                self._write_summary_locked(force=force_summary)
            except Exception:
                # Сбой краткой сводки не должен ломать основной JSONL-журнал.
                pass


    def write_exception(self, event: str, exc: Exception, message: str = "", **details):
        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.event(
            event,
            level="error",
            message=message or compact_exception(exc),
            exception={
                "type": type(exc).__name__,
                "category": classify_exception(exc),
                "message": compact_exception(exc, max_len=2000),
                "chain": exception_chain(exc),
                "traceback": redact_diagnostic_text(trace),
            },
            **details,
        )


    def close(self):
        try:
            self.event("app_session_finished", message="Программа закрывает диагностический журнал.")
            with self.lock:
                try:
                    self._write_summary_locked(force=True)
                except Exception:
                    pass
                if hasattr(self, "file") and not self.file.closed:
                    self.file.close()
                if getattr(self, "problem_file", None) is not None and not self.problem_file.closed:
                    self.problem_file.close()
        except Exception:
            pass
