"""Method owner for ProblemLogger: _manifest_payload_locked, _write_manifest_locked, _write_latest_index_locked, _markdown_cell, _render_report_locked, _update_summary_locked, _sync_stored_sample_locked, _write_summary_locked."""

from __future__ import annotations

import time
from videotranslator.config import PROBLEM_REPORT_REFRESH_SEC
from videotranslator.core.io import atomic_write_json, atomic_write_text

class ProblemSummaryMixin:
    """Focused behavior-preserving mixin extracted for AI-local navigation."""

    def _update_summary_locked(self, record: dict):
        level = str(record.get("level") or "info")
        event = str(record.get("event") or "")
        details = record.get("details") or {}
        timestamp = record.get("timestamp") or ""
        self._summary["updated_at"] = record.get("timestamp") or ""
        self._summary["last_event"] = {
            "event": event,
            "level": level,
            "message": record.get("message") or "",
            "sequence": record.get("sequence"),
        }
        self._summary["counters"][level] = int(self._summary["counters"].get(level, 0)) + 1
        event_counts = self._summary["event_counts_by_event"]
        event_counts[event] = int(event_counts.get(event, 0)) + 1
        quality = self._summary["diagnostics_quality"]
        quality["events_total"] = int(quality.get("events_total") or 0) + 1
        if details.get("stage"):
            self._summary["current_stage"] = details.get("stage")
            self._summary["current_file"]["stage"] = details.get("stage")
        if details.get("input_path"):
            self._summary["current_input_path"] = details.get("input_path")
            self._summary["current_file"]["input_path"] = details.get("input_path")

        if event == "batch_started":
            raw_files = details.get("files") or []
            if isinstance(raw_files, dict):
                raw_files = raw_files.get("items") or []
            raw_states = details.get("file_states") or []
            if isinstance(raw_states, dict):
                raw_states = raw_states.get("items") or []
            if raw_states:
                files = [dict(item) for item in raw_states]
            else:
                files = [
                    {"file_index": index, "input_path": path, "status": "pending"}
                    for index, path in enumerate(raw_files, 1)
                ]
            total = int(details.get("files_count") or len(files))
            statuses = [item.get("status") for item in files]
            self._summary["batch"] = {
                "status": "running",
                "batch_id": details.get("batch_id") or "",
                "resumed": bool(details.get("resumed")),
                "total": total,
                "started": 0,
                "successful": statuses.count("succeeded"),
                "failed": statuses.count("failed"),
                "pending": sum(status != "succeeded" for status in statuses),
                "current_index": 0,
                "output_dir": details.get("output_dir") or "",
                "files": files,
            }
        elif event == "batch_file_started":
            batch = self._summary["batch"]
            index = int(details.get("file_index") or 0)
            batch["status"] = "running"
            batch["current_index"] = index
            batch["started"] = max(int(batch.get("started") or 0), index)
            for item in batch.get("files", []):
                if int(item.get("file_index") or 0) == index:
                    item.update({
                        "input_path": details.get("input_path") or item.get("input_path") or "",
                        "output_path": details.get("output_path") or "",
                        "status": "processing",
                    })
                    break
        elif event == "batch_file_finished":
            batch = self._summary["batch"]
            index = int(details.get("file_index") or 0)
            success = bool(details.get("success"))
            for item in batch.get("files", []):
                if int(item.get("file_index") or 0) == index:
                    item.update({
                        "input_path": details.get("input_path") or item.get("input_path") or "",
                        "output_path": details.get("output_path") or item.get("output_path") or "",
                        "status": "succeeded" if success else "failed",
                        "elapsed_sec": details.get("elapsed_sec"),
                    })
                    break
            statuses = [item.get("status") for item in batch.get("files", [])]
            batch["successful"] = statuses.count("succeeded")
            batch["failed"] = sum(status in {"failed", "missing"} for status in statuses)
            batch["pending"] = sum(status != "succeeded" for status in statuses)
        elif event == "batch_finished":
            batch = self._summary["batch"]
            successful = int(details.get("successful") or 0)
            total = int(details.get("total") or batch.get("total") or 0)
            cancelled = bool(details.get("cancelled"))
            batch.update({
                "status": "cancelled" if cancelled else ("completed" if successful == total else "completed_with_errors"),
                "successful": successful,
                "failed": int(details.get("failed") or 0),
                "pending": int(details.get("pending") or 0),
                "elapsed_sec": details.get("elapsed_sec"),
            })

        if event == "file_pipeline_started":
            settings = details.get("settings") or {}
            self._summary["current_file"] = {
                "status": "running",
                "stage": details.get("stage") or "initialization",
                "input_path": details.get("input_path") or self._summary.get("current_input_path") or "",
                "output_path": details.get("output_path") or "",
            }
            self._summary["selected_video_encoder"] = {}
            self._summary["tts_cache"] = {}
            self._summary["tts_voice"] = {
                "status": "idle",
                "selected_voice": settings.get("voice") or "",
                "fallback_provider": "",
                "voice_may_differ": False,
            }
        elif event in {"file_pipeline_finished", "file_pipeline_failed", "file_pipeline_cancelled"}:
            self._summary["current_file"]["status"] = event.removeprefix("file_pipeline_")
            self._summary["current_file"]["elapsed_sec"] = details.get("elapsed_sec")
            if details.get("final_path"):
                self._summary["current_file"]["output_path"] = details.get("final_path")
        elif event == "app_session_finished":
            self._summary["status"] = "closed"
            self._summary["session_status"] = "closed"
            self._summary["current_stage"] = "closed"
            self._summary["current_file"]["stage"] = "closed"
            # A closed application session cannot have a literally active network incident.
            # Preserve the diagnostic truth by marking unresolved storms explicitly instead
            # of leaving ``active=true`` forever, which misleads ChatGPT/Codex on next read.
            network = self._summary.get("network") or {}
            unresolved = 0
            for service, state in (network.get("active_services") or {}).items():
                if isinstance(state, dict) and state.get("active"):
                    unresolved += 1
                    state.update({
                        "active": False,
                        "unresolved_at_close": True,
                        "closed_at": timestamp,
                    })
            if unresolved:
                network["incidents_unresolved_at_close"] = int(
                    network.get("incidents_unresolved_at_close") or 0
                ) + unresolved
        elif event == "app_close_requested" and details.get("processing"):
            self._summary["batch"]["status"] = "cancellation_requested"
        if event == "video_encoder_selected":
            self._summary["selected_video_encoder"] = {
                "input_path": details.get("input_path") or self._summary.get("current_input_path") or "",
                "name": details.get("encoder_name") or "",
                "processing_speed_x": details.get("processing_speed_x"),
                "elapsed_sec": details.get("elapsed_sec"),
                "command": details.get("command") or [],
            }
        if event == "tts_voice_preservation_started":
            self._summary["tts_voice"] = {
                "status": "waiting_for_edge_tts",
                "selected_voice": details.get("voice") or "",
                "fallback_provider": "",
                "voice_may_differ": False,
                "wait_sec": details.get("wait_sec"),
                "incident": details.get("incident"),
            }
        elif event == "tts_edge_probe_started":
            self._summary["tts_voice"]["status"] = "checking_edge_tts"
        elif event in {"tts_voice_preservation_recovered", "network_storm_finished"}:
            if event != "network_storm_finished" or details.get("service") == "edge_tts":
                self._summary["tts_voice"]["status"] = "selected_voice_restored"
        elif event == "tts_gtts_fallback_enabled":
            self._summary["tts_voice"] = {
                "status": "gtts_fallback",
                "selected_voice": details.get("voice") or self._summary["tts_voice"].get("selected_voice", ""),
                "fallback_provider": "gtts",
                "voice_may_differ": True,
                "reason": details.get("reason") or "",
                "incident": details.get("incident"),
            }
        elif event == "tts_summary":
            self._summary["tts_voice"].update({
                "status": details.get("voice_consistency") or "finished",
                "selected_voice": details.get("selected_voice") or self._summary["tts_voice"].get("selected_voice", ""),
                "fallback_provider": "gtts" if details.get("gtts_fallback", 0) else "",
                "voice_may_differ": bool(details.get("gtts_fallback", 0)),
                "gtts_segments": details.get("gtts_fallback", 0),
            })
        if event == "pause_sync_quality_summary":
            self._summary["pause_sync"] = {
                "pause_count": int(details.get("pause_count") or 0),
                "total_pause_sec": details.get("total_pause_sec"),
                "source_video_duration_sec": details.get("source_video_duration_sec"),
                "pause_ratio": details.get("pause_ratio"),
                "severe_pause_count": int(details.get("severe_pause_count") or 0),
                "max_pause_sec": details.get("max_pause_sec"),
                "top_pause_segments": details.get("top_pause_segments") or [],
            }

        if event in {
            "tts_cache_maintenance_finished",
            "tts_cache_released_after_success",
            "tts_prepared_cache_released_after_stop",
        }:
            self._summary["tts_cache"] = {
                "event": event,
                "input_path": details.get("input_path") or self._summary.get("current_input_path") or "",
                "deleted_entries": details.get("deleted_entries", 0),
                "deleted_files": details.get("deleted_files", 0),
                "freed_bytes": details.get("freed_bytes", 0),
                "remaining_bytes": details.get("remaining_bytes"),
                "errors": details.get("errors") or [],
            }

        if event == "network_storm_started":
            network = self._summary["network"]
            service = str(details.get("service") or details.get("component") or "network")
            network["incidents_started"] = int(network.get("incidents_started") or 0) + 1
            network["active_services"][service] = {
                "active": True,
                "started_at": timestamp,
                "failures_in_window": details.get("failures_in_window"),
                "pause_sec": details.get("pause_sec"),
            }
        elif event == "network_storm_finished":
            network = self._summary["network"]
            service = str(details.get("service") or details.get("component") or "network")
            network["incidents_finished"] = int(network.get("incidents_finished") or 0) + 1
            network["active_services"][service] = {
                "active": False,
                "finished_at": timestamp,
                "duration_sec": details.get("duration_sec"),
                "successful_requests": details.get("successful_requests"),
            }

        if level in {"warning", "error"}:
            quality["problem_events_total"] = int(quality.get("problem_events_total") or 0) + 1
            counts = self._summary["problem_counts_by_event"]
            counts[event] = int(counts.get(event, 0)) + 1
            recent = self._summary["recent_problems"]
            exception = details.get("exception") or {}
            diagnostic = record.get("diagnostic") or {}
            category = exception.get("category") or diagnostic.get("family") or "runtime"
            category_counts = self._summary["problem_counts_by_category"]
            category_counts[category] = int(category_counts.get(category, 0)) + 1
            message = record.get("message") or ""
            signature = str(diagnostic.get("signature") or self._event_signature(
                event, level, str(diagnostic.get("family") or category), message, details
            ))
            if signature not in self._problem_signatures_seen:
                self._problem_signatures_seen.add(signature)
                quality["unique_problem_signatures_total"] = len(self._problem_signatures_seen)
            occurrence = int(diagnostic.get("occurrence") or 1)
            stage = details.get("stage") or self._summary.get("current_stage") or ""
            input_path = details.get("input_path") or self._summary.get("current_input_path") or ""
            if stage:
                quality["problems_with_stage"] = int(quality.get("problems_with_stage") or 0) + 1
            if input_path:
                quality["problems_with_input_path"] = int(quality.get("problems_with_input_path") or 0) + 1
            if not stage and not input_path:
                quality["problems_missing_stage_and_input"] = int(
                    quality.get("problems_missing_stage_and_input") or 0
                ) + 1
            if isinstance(exception, dict) and exception.get("traceback"):
                quality["exceptions_with_traceback"] = int(
                    quality.get("exceptions_with_traceback") or 0
                ) + 1
            if level == "error":
                quality["error_events_total"] = int(quality.get("error_events_total") or 0) + 1
                if not (isinstance(exception, dict) and exception.get("traceback")):
                    quality["errors_without_traceback"] = int(
                        quality.get("errors_without_traceback") or 0
                    ) + 1
            existing = next((item for item in recent if item.get("signature") == signature), None)
            if existing is None and occurrence > 1:
                quality["problem_signatures_outside_recent_window"] = int(
                    quality.get("problem_signatures_outside_recent_window") or 0
                ) + 1
            segment_index = details.get("segment_index")
            if segment_index is None:
                segment_index = details.get("tts_segment_index")
            context = self._compact_problem_context(details)
            compact_exception = self._compact_exception_evidence(exception)
            if existing:
                existing.update({
                    "last_timestamp": timestamp,
                    "last_sequence": record.get("sequence"),
                    "count": int(existing.get("occurrences_total") or existing.get("count") or 1) + 1,
                    "occurrences_total": int(
                        existing.get("occurrences_total") or existing.get("count") or 1
                    ) + 1,
                    "stored_samples": int(existing.get("stored_samples") or 0),
                    "last_segment_index": segment_index,
                    "last_exception_message": compact_exception.get("message") or "",
                    "last_context": context,
                })
                recent.remove(existing)
                recent.append(existing)
            else:
                recent.append({
                    "signature": signature,
                    "first_timestamp": timestamp,
                    "last_timestamp": timestamp,
                    "first_sequence": record.get("sequence"),
                    "last_sequence": record.get("sequence"),
                    "count": occurrence,
                    "occurrences_total": occurrence,
                    "stored_samples": 0,
                    "event": event,
                    "level": level,
                    "message": message,
                    "category": category,
                    "family": diagnostic.get("family") or category,
                    "stage": stage,
                    "input_path": input_path,
                    "last_segment_index": segment_index,
                    "exception": compact_exception,
                    "context": context,
                })
            del recent[:-20]

    def _sync_stored_sample_locked(self, record: dict):
        diagnostic = record.get("diagnostic") or {}
        signature = diagnostic.get("signature") or ""
        if not signature:
            return
        item = next(
            (problem for problem in self._summary.get("recent_problems", []) if problem.get("signature") == signature),
            None,
        )
        if item:
            item["stored_samples"] = int(item.get("stored_samples") or 0) + 1

    def _write_summary_locked(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last_summary_write < 1.0:
            return
        self._summary["codex_analysis"] = self._build_codex_analysis_locked()
        atomic_write_json(self.summary_path, self._summary)
        artifact_errors_before = len(
            self._summary["diagnostics_quality"].get("artifact_write_errors") or []
        )
        session_terminal = self._summary.get("session_status") != "running"
        refresh_readable_artifacts = (
            session_terminal
            or not self.report_path.exists()
            or now - self._last_report_write >= PROBLEM_REPORT_REFRESH_SEC
        )
        if refresh_readable_artifacts:
            try:
                atomic_write_text(self.report_path, self._render_report_locked())
                self._last_report_write = now
            except Exception as exc:
                self._record_artifact_error_locked("report.md", exc)
            try:
                self._write_manifest_locked()
            except Exception as exc:
                self._record_artifact_error_locked("manifest.json", exc)
        try:
            self._write_latest_index_locked()
        except Exception as exc:
            self._record_artifact_error_locked("latest_session.json", exc)
        if len(self._summary["diagnostics_quality"].get("artifact_write_errors") or []) > artifact_errors_before:
            self._summary["codex_analysis"] = self._build_codex_analysis_locked()
            try:
                atomic_write_json(self.summary_path, self._summary)
            except Exception:
                pass
        self._last_summary_write = now
