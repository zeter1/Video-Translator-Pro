import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

import video_translator as vt


def setUpModule():
    # VideoTranslator constructs a cache before tests can replace the instance.
    # Never let a regression test create/maintain the user's real recovery cache.
    directory = tempfile.TemporaryDirectory(prefix="vt_regression_cache_")
    unittest.addModuleCleanup(directory.cleanup)
    patcher = mock.patch("videotranslator.tts.cache.get_tts_cache_dir",
                         return_value=Path(directory.name))
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    stream_patcher = mock.patch("videotranslator.pipeline.process.select_audio_stream", return_value=1)
    stream_patcher.start()
    unittest.addModuleCleanup(stream_patcher.stop)


class ProblemLoggerTests(unittest.TestCase):
    def test_each_session_has_own_complete_codex_artifact_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                first = vt.ProblemLogger()
                first.event(
                    "file_pipeline_failed",
                    level="error",
                    message="FFmpeg failed",
                    stage="final_mux",
                    input_path="C:/video.mp4",
                    command=["ffmpeg", "-i", "video.mp4"],
                    returncode=1,
                    stderr="Invalid data",
                )
                first_dir = first.session_dir
                first.close()

                second = vt.ProblemLogger()
                second_dir = second.session_dir
                second.close()

            self.assertNotEqual(first_dir, second_dir)
            for session_dir in (first_dir, second_dir):
                self.assertEqual(
                    {"events.jsonl", "problems.jsonl", "summary.json", "report.md", "manifest.json"},
                    {path.name for path in session_dir.iterdir()},
                )
            self.assertTrue((directory / "README_FOR_CODEX.md").exists())
            latest = json.loads((directory / "latest_session.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["session_id"], second.session_id)
            self.assertEqual(latest["status"], "closed")
            self.assertTrue((directory / latest["start_here"]).exists())

            problem_records = [
                json.loads(line)
                for line in (first_dir / "problems.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([record["event"] for record in problem_records], ["file_pipeline_failed"])
            self.assertEqual(problem_records[0]["diagnostic"]["family"], "external_process")
            report = (first_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("Что можно улучшить в программе", report)
            self.assertIn("ffmpeg_pipeline", report)

    def test_jsonl_is_split_by_level_and_redacts_tokens(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                logger = vt.ProblemLogger()
                logger.event(
                    "network_test",
                    level="warning",
                    message="https://example.test/api?access_token=secret-value",
                    api_key="secret-value",
                )
                logger.event(
                    "video_encoder_selected",
                    encoder_name="NVIDIA NVENC H.264",
                    processing_speed_x=2.5,
                    elapsed_sec=12.0,
                    command=["ffmpeg", "-c:v", "h264_nvenc"],
                )
                path = logger.path
                logger.close()

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            problems = [
                json.loads(line) for line in logger.problem_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[0]["event"], "app_session_started")
            self.assertEqual([record["event"] for record in problems], ["network_test"])
            self.assertNotIn("secret-value", path.read_text(encoding="utf-8"))
            self.assertNotIn("secret-value", logger.problem_path.read_text(encoding="utf-8"))
            self.assertEqual(records[-1]["event"], "app_session_finished")
            self.assertNotIn("session_id", records[0])
            self.assertIsInstance(records[0]["thread"], str)

            summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "closed")
            self.assertEqual(summary["recent_problems"][-1]["event"], "network_test")
            self.assertEqual(summary["selected_video_encoder"]["name"], "NVIDIA NVENC H.264")

    def test_problem_summary_tracks_voice_fallback_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                logger = vt.ProblemLogger()
                logger.event(
                    "tts_voice_preservation_started",
                    level="warning",
                    voice="ru-RU-DmitryNeural",
                    wait_sec=120,
                    incident=1,
                )
                logger.event(
                    "tts_gtts_fallback_enabled",
                    level="warning",
                    voice="ru-RU-DmitryNeural",
                    reason="edge_wait_timeout",
                    incident=1,
                )
                logger.close()

            summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["tts_voice"]["status"], "gtts_fallback")
            self.assertEqual(summary["tts_voice"]["fallback_provider"], "gtts")
            self.assertTrue(summary["tts_voice"]["voice_may_differ"])

    def test_closed_session_marks_active_network_storm_as_unresolved_not_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                logger = vt.ProblemLogger()
                logger.event(
                    "network_storm_started",
                    level="warning",
                    service="edge_tts",
                    failures_in_window=3,
                    pause_sec=30,
                )
                logger.close()

            summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
            edge_state = summary["network"]["active_services"]["edge_tts"]
            self.assertFalse(edge_state["active"])
            self.assertTrue(edge_state["unresolved_at_close"])
            self.assertEqual(summary["network"]["incidents_unresolved_at_close"], 1)

    def test_pause_sync_quality_is_saved_and_rendered_for_codex(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                logger = vt.ProblemLogger()
                logger.event(
                    "pause_sync_quality_summary",
                    pause_count=214,
                    total_pause_sec=211.521,
                    source_video_duration_sec=3102.86,
                    pause_ratio=0.06817,
                    severe_pause_count=21,
                    max_pause_sec=7.48,
                    top_pause_segments=[
                        {"segments": [498], "at_sec": 2948.0, "duration_sec": 7.48},
                        {"segments": [511], "at_sec": 3031.0, "duration_sec": 6.80},
                    ],
                )
                logger.close()

            summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["pause_sync"]["pause_count"], 214)
            self.assertEqual(summary["pause_sync"]["severe_pause_count"], 21)
            report = logger.report_path.read_text(encoding="utf-8")
            self.assertIn("## Качество Pause Sync", report)
            self.assertIn("6.8%", report)
            self.assertIn("seg 498: +7.48с", report)
            self.assertIn("pause_sync_quality", report)

    def test_problem_summary_tracks_tts_cache_cleanup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                logger = vt.ProblemLogger()
                logger.event(
                    "tts_cache_released_after_success",
                    deleted_entries=12,
                    deleted_files=24,
                    freed_bytes=4096,
                    remaining_bytes=0,
                    errors=[],
                )
                logger.close()

            summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["tts_cache"]["deleted_entries"], 12)
            self.assertEqual(summary["tts_cache"]["deleted_files"], 24)
            self.assertEqual(summary["tts_cache"]["remaining_bytes"], 0)

    def test_stale_running_session_is_archived_and_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            previous = {
                "schema_version": 1,
                "session_id": "old-session-123",
                "status": "running",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "current_stage": "tts_and_timeline",
                "current_input_path": "C:/video.mp4",
                "problem_log": "C:/problems_old.jsonl",
            }
            (directory / "latest_problem_summary.json").write_text(
                json.dumps(previous), encoding="utf-8"
            )

            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                logger = vt.ProblemLogger()
                path = logger.path
                logger.close()

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            problems = [
                json.loads(line) for line in logger.problem_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[0]["event"], "app_session_started")
            self.assertEqual(problems[0]["event"], "previous_session_interrupted")
            archived = json.loads(
                (directory / "legacy_flat_logs" / "latest_problem_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(archived["status"], "interrupted")

    def test_summary_resets_per_file_state_and_tracks_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                logger = vt.ProblemLogger()
                logger.event(
                    "batch_started",
                    files_count=2,
                    files=["C:/one.mp4", "C:/two.mp4"],
                    output_dir="C:/out",
                )
                logger.event(
                    "file_pipeline_started",
                    input_path="C:/one.mp4",
                    output_path="C:/out/one_RU.mp4",
                    stage="initialization",
                    settings={"voice": "voice-one"},
                )
                logger.event("video_encoder_selected", input_path="C:/one.mp4", encoder_name="libx264")
                logger.event(
                    "tts_cache_released_after_success",
                    input_path="C:/one.mp4",
                    deleted_entries=4,
                )
                logger.event(
                    "batch_file_finished",
                    file_index=1,
                    files_total=2,
                    input_path="C:/one.mp4",
                    success=True,
                )
                logger.event(
                    "file_pipeline_started",
                    input_path="C:/two.mp4",
                    output_path="C:/out/two_RU.mp4",
                    stage="initialization",
                    settings={"voice": "voice-two"},
                )
                summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
                logger.close()

            self.assertEqual(summary["current_input_path"], "C:/two.mp4")
            self.assertEqual(summary["current_file"]["status"], "running")
            self.assertEqual(summary["batch"]["successful"], 1)
            self.assertEqual(summary["selected_video_encoder"], {})
            self.assertEqual(summary["tts_cache"], {})
            self.assertEqual(summary["tts_voice"]["selected_voice"], "voice-two")

    def test_repeated_problems_are_grouped_and_long_lists_report_truncation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                logger = vt.ProblemLogger()
                for _ in range(2):
                    logger.event(
                        "tts_retry",
                        level="warning",
                        message="Edge timeout",
                        failed_indices=list(range(350)),
                        exception={"category": "network_timeout"},
                    )
                path = logger.path
                logger.close()

            records = [
                json.loads(line) for line in logger.problem_path.read_text(encoding="utf-8").splitlines()
            ]
            truncated = records[0]["details"]["failed_indices"]
            self.assertTrue(truncated["truncated"])
            self.assertEqual(truncated["total_count"], 350)
            summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
            grouped = [item for item in summary["recent_problems"] if item["event"] == "tts_retry"]
            self.assertEqual(len(grouped), 1)
            self.assertEqual(grouped[0]["count"], 2)
            self.assertEqual(grouped[0]["occurrences_total"], 2)
            self.assertEqual(grouped[0]["stored_samples"], 2)

    def test_repeated_problem_sampling_keeps_exact_totals_and_reduces_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                logger = vt.ProblemLogger()
                for index in range(100):
                    logger.event(
                        "translation_retry",
                        level="warning",
                        message="Translation timed out",
                        input_path="C:/long-video.mp4",
                        stage="translation",
                        segment_index=index,
                        attempt=(index % 3) + 1,
                        exception={
                            "type": "TimeoutError",
                            "category": "network_timeout",
                            "message": "HTTPS request timed out after 35 seconds",
                        },
                    )
                logger.close()

            problems = [
                json.loads(line) for line in logger.problem_path.read_text(encoding="utf-8").splitlines()
            ]
            summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
            group = next(item for item in summary["recent_problems"] if item["event"] == "translation_retry")
            self.assertEqual(group["occurrences_total"], 100)
            self.assertEqual(group["stored_samples"], 8)
            self.assertEqual(len(problems), 8)
            self.assertEqual(summary["diagnostics_quality"]["repeat_events_compacted"], 92)
            self.assertGreater(summary["diagnostics_quality"]["estimated_bytes_saved"], 0)
            self.assertEqual(
                [record["diagnostic"]["occurrence"] for record in problems],
                [1, 2, 3, 5, 10, 20, 50, 100],
            )
            cards = summary["codex_analysis"]["issue_cards"]
            self.assertEqual(cards[0]["family"], "network_timeout")
            self.assertEqual(cards[0]["playbook"], "network_retry")
            self.assertIn(
                "NetworkStormGuard",
                summary["codex_analysis"]["remediation_playbooks"]["network_retry"]["code_search"],
            )
            self.assertIn("Карточки исправления проблем", logger.report_path.read_text(encoding="utf-8"))


    def test_translation_provider_error_has_ai_readable_root_cause_card(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                logger = vt.ProblemLogger()
                logger.event(
                    "translation_provider_response_rejected",
                    level="warning",
                    message="Сервис вернул страницу ошибки вместо перевода.",
                    input_path="C:/video.mp4",
                    stage="translation",
                    failure_kind="provider_response_invalid",
                    impact="blocked_before_checkpoint_and_tts",
                    recovery_action="retry_same_segment_then_defer",
                    segment_index=25,
                    attempt=1,
                    attempts_total=3,
                    response_validation={
                        "valid": False,
                        "reason": "provider_error_page_500",
                        "signals": ["server_error_phrase", "google_error_sentence", "http_status_500"],
                        "response_length": 109,
                        "response_sha256": "abc",
                        "preview": "Error 500 (Server Error)...",
                    },
                )
                logger.close()

            record = json.loads(logger.problem_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["diagnostic"]["pipeline_stage"], "translation")
            self.assertEqual(record["diagnostic"]["failure_kind"], "provider_response_invalid")
            self.assertEqual(record["diagnostic"]["impact"], "blocked_before_checkpoint_and_tts")
            summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
            card = summary["codex_analysis"]["issue_cards"][0]
            self.assertEqual(card["playbook"], "translation_provider_response")
            self.assertEqual(card["cause_status"], "confirmed_invalid_provider_payload")
            self.assertEqual(
                card["confirmed_evidence"]["context"]["response_validation"]["reason"],
                "provider_error_page_500",
            )
            report = logger.report_path.read_text(encoding="utf-8")
            self.assertIn("translation_response=provider_error_page_500", report)
            self.assertIn("Контракт анализа для ChatGPT / Codex", report)

    def test_media_validation_problem_explains_ffmpeg_success_and_duration_delta(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            with mock.patch.object(vt, "get_problem_logs_dir", return_value=directory):
                logger = vt.ProblemLogger()
                logger.event(
                    "video_output_validation_failed",
                    level="warning",
                    message="MP4 не прошёл проверку A/V-потоков.",
                    input_path="C:/source.mp4",
                    stage="final_video_assembly",
                    encoder_name="libx264 quality",
                    return_code=0,
                    ffmpeg_completed_ok=True,
                    expected_video_duration_sec=428.397,
                    expected_audio_duration_sec=429.040,
                    validation={
                        "valid": False,
                        "reason": "video_duration_mismatch",
                        "checks": [{
                            "stream": "video",
                            "end_delta_sec": -0.643,
                            "tolerance": 0.12,
                        }],
                    },
                )
                logger.close()

            summary = json.loads(logger.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["problem_counts_by_category"]["media_validation"], 1)
            card = summary["codex_analysis"]["issue_cards"][0]
            self.assertEqual(card["playbook"], "media_validation")
            context = card["confirmed_evidence"]["context"]
            self.assertEqual(context["return_code"], 0)
            self.assertTrue(context["ffmpeg_completed_ok"])
            self.assertEqual(context["validation"]["reason"], "video_duration_mismatch")
            report = logger.report_path.read_text(encoding="utf-8")
            self.assertIn("ffmpeg_completed_ok=True", report)
            self.assertIn("video_delta=-0.643с", report)

    def test_failed_subprocess_log_has_code_command_and_stderr(self):
        messages = []
        result = vt.run_subprocess(
            [sys.executable, "-c", "import sys; sys.stderr.write('failure-detail'); raise SystemExit(7)"],
            timeout=10,
            log=messages.append,
        )

        combined = "\n".join(messages)
        self.assertEqual(result.returncode, 7)
        self.assertIn("кодом 7", combined)
        self.assertIn("Команда внешнего процесса", combined)
        self.assertIn("failure-detail", combined)


class TranslationCheckpointTests(unittest.TestCase):
    def test_checkpoint_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            input_path = directory / "video.mp4"
            input_path.write_bytes(b"test-video")
            checkpoint_path = directory / "checkpoint.json"
            segments = [{
                "start": 1.0,
                "end": 2.5,
                "source": "Hello world",
                "translated": "Привет, мир",
            }]

            vt.save_translation_checkpoint(checkpoint_path, str(input_path), "en", "ru", segments)
            loaded = vt.load_translation_checkpoint(checkpoint_path)
            key = vt.translation_segment_key(1.0, 2.5, "Hello world")

            self.assertEqual(loaded[key], "Привет, мир")
            self.assertFalse(list(directory.glob("*.tmp")))

    def test_translation_retry_is_bounded_and_reported(self):
        class FlakyTranslator:
            def __init__(self):
                self.calls = 0

            def translate(self, _text):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("network timed out")
                return "Перевод готов"

        flaky = FlakyTranslator()
        fake_module = types.SimpleNamespace(GoogleTranslator=lambda **_kwargs: flaky)
        events = []
        translator = vt.VideoTranslator(
            lambda _message: None,
            cancel_event=threading.Event(),
            problem_cb=lambda event, **details: events.append((event, details)),
        )
        translator.target_info = {"code": "ru"}

        with mock.patch.dict(sys.modules, {"deep_translator": fake_module}):
            with mock.patch.object(translator, "_sleep_or_cancel", return_value=None):
                result = translator.translate_segment("Hello", index=7, attempts=2)

        self.assertEqual(result, "Перевод готов")
        self.assertEqual(flaky.calls, 2)
        self.assertTrue(any(event == "translation_retry" for event, _details in events))
        self.assertTrue(any(event == "translation_retry_recovered" for event, _details in events))

    def test_one_failed_segment_stops_incomplete_video_but_keeps_checkpoint(self):
        class FakeWhisperModel:
            def transcribe(self, *_args, **_kwargs):
                return {
                    "language": "en",
                    "text": "source text",
                    "segments": [
                        {"start": float(index * 2), "end": float(index * 2 + 2), "text": f"text {index}"}
                        for index in range(5)
                    ],
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            input_path = directory / "video.mp4"
            output_path = directory / "video_RU.mp4"
            checkpoint_path = directory / "checkpoint.json"
            input_path.write_bytes(b"test-video")
            events = []
            messages = []
            translator = vt.VideoTranslator(
                messages.append,
                cancel_event=threading.Event(),
                problem_cb=lambda event, **details: events.append((event, details)),
            )
            translator.tts_cache = vt.TTSCache(directory / "tts_cache")

            def fake_translate(_text, index=0, attempts=3):
                if index == 3:
                    raise TimeoutError("network timed out")
                return f"перевод {index}"

            translator.translate_segment = fake_translate
            translator.build_timeline = mock.Mock(return_value=(str(directory / "voice.wav"), [], 10.0))

            with mock.patch.object(vt, "find_ffmpeg", return_value="ffmpeg"), \
                    mock.patch.object(vt, "find_ffprobe", return_value="ffprobe"), \
                    mock.patch("videotranslator.pipeline.process.select_audio_stream", return_value=0), \
                    mock.patch.object(vt, "get_media_duration", return_value=10.0), \
                    mock.patch.object(vt, "extract_audio_for_whisper", return_value=True), \
                    mock.patch.object(vt, "get_whisper_model", return_value=FakeWhisperModel()), \
                    mock.patch.object(vt, "translation_checkpoint_path", return_value=checkpoint_path), \
                    mock.patch.object(vt, "write_translated_text_file", return_value="translation.txt"), \
                    mock.patch.object(vt, "assemble_final_video", return_value=str(output_path)), \
                    mock.patch.object(vt, "write_translation_report", return_value=str(directory / "report.txt")), \
                    mock.patch.object(translator, "_sleep_or_cancel", return_value=None):
                result = translator.process(
                    str(input_path),
                    str(output_path),
                    "ru-RU-DmitryNeural",
                    "small",
                    False,
                    0,
                    target_info=vt.get_target_language("Русский"),
                    review_before_tts=False,
                )

            self.assertFalse(result)
            self.assertTrue(checkpoint_path.exists())
            self.assertTrue(any(event == "translation_segments_failed" for event, _details in events))
            self.assertTrue(any("неполное видео не создаётся" in message.lower() for message in messages))


class BatchRecoveryTests(unittest.TestCase):
    @staticmethod
    def _worker_app():
        app = object.__new__(vt.App)
        app.last_log_path = ""
        app.problem_logger = None
        app.file_logger = None
        app._cancel_event = threading.Event()
        app._active_translator = None
        app._resume_batch_state = None
        app._log = mock.Mock()
        app._problem = mock.Mock()
        app._safe_after = mock.Mock()
        app._threadsafe_progress = mock.Mock()
        app._threadsafe_network_notice = mock.Mock()
        app._close_vpn_notice = mock.Mock()
        app.review_translations = mock.Mock()
        return app

    def test_batch_state_round_trip_keeps_completed_files_out_of_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            first = directory / "one.mp4"
            second = directory / "two.mp4"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            state_path = directory / "latest_batch_recovery.json"

            state = vt.create_batch_recovery_state(
                [str(first), str(second)],
                str(directory / "out"),
                {"voice": "ru-RU-DmitryNeural", "model": "small"},
            )
            state["files"][0]["status"] = "succeeded"
            vt.save_batch_recovery_state(state, state_path)
            loaded = vt.load_batch_recovery_state(state_path)
            pending = vt.batch_recovery_pending_entries(loaded)

            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["input"]["path"], str(second.resolve()))
            self.assertFalse(list(directory.glob("*.tmp")))

    def test_old_problem_log_recovery_ignores_empty_or_directory_path(self):
        # Regression: Path("") == Path("."), and Path(".").with_name(...) raises
        # ValueError on Windows. Startup recovery must be a no-op, not a Tk callback crash.
        self.assertEqual(vt.reconstruct_batch_recovery_state_from_problem_log(""), {})
        self.assertEqual(vt.reconstruct_batch_recovery_state_from_problem_log(Path(".")), {})

    def test_offer_batch_recovery_with_empty_previous_problem_log_is_safe(self):
        # Mirrors the Tkinter startup callback from the Windows crash report.
        app = object.__new__(vt.App)
        app._closing = False
        app._processing = False
        app.problem_logger = types.SimpleNamespace(
            _summary={"previous_session": {"problem_log": ""}}
        )
        app._problem = mock.Mock()

        with mock.patch.object(vt, "load_batch_recovery_state", return_value={}):
            app._offer_batch_recovery()

    def test_old_problem_log_reconstructs_unfinished_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            first = directory / "one.mp4"
            second = directory / "two.mp4"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            output_dir = directory / "out"
            output_dir.mkdir()
            first_output = output_dir / "one_RU.mp4"
            second_output = output_dir / "two_RU.mp4"
            log_path = directory / "problems_old.jsonl"
            records = [
                {
                    "session_id": "old-session",
                    "event": "batch_started",
                    "details": {
                        "files": [str(first), str(second)],
                        "output_dir": str(output_dir),
                        "settings": {"voice": "voice", "model": "small"},
                    },
                },
                {
                    "event": "batch_file_started",
                    "details": {"input_path": str(first), "output_path": str(first_output)},
                },
                {
                    "event": "file_pipeline_finished",
                    "details": {"input_path": str(first), "final_path": str(first_output)},
                },
                {
                    "event": "batch_file_finished",
                    "details": {"input_path": str(first), "success": True, "elapsed_sec": 12},
                },
                {
                    "event": "batch_file_started",
                    "details": {"input_path": str(second), "output_path": str(second_output)},
                },
            ]
            log_path.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
                encoding="utf-8",
            )

            state = vt.reconstruct_batch_recovery_state_from_problem_log(log_path)

            self.assertEqual(state["status"], "interrupted")
            self.assertEqual(state["files"][0]["status"], "succeeded")
            self.assertEqual(state["files"][0]["output_path"], str(first_output))
            self.assertEqual(state["files"][1]["status"], "pending")
            self.assertEqual(state["files"][1]["output_path"], str(second_output))

    def test_worker_saves_successful_batch_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            input_path = directory / "one.mp4"
            input_path.write_bytes(b"video")
            output_dir = directory / "out"
            output_dir.mkdir()
            state_path = directory / "batch.json"
            state = vt.create_batch_recovery_state(
                [str(input_path)], str(output_dir), {"voice": "voice", "model": "small"}
            )
            fake_translator = mock.Mock()
            fake_translator.process.return_value = True
            app = self._worker_app()

            with mock.patch.object(vt, "VideoTranslator", return_value=fake_translator), \
                    mock.patch.object(vt, "get_batch_recovery_state_path", return_value=state_path):
                app._worker(
                    state,
                    str(output_dir),
                    "voice",
                    "small",
                    False,
                    0,
                    vt.get_target_language("Русский"),
                    False,
                    vt.normalize_audio_settings(),
                )

            saved = vt.load_batch_recovery_state(state_path)
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(saved["files"][0]["status"], "succeeded")
            self.assertIsNone(app._resume_batch_state)

    def test_worker_reuses_only_verified_existing_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            input_path = directory / "one.mp4"
            input_path.write_bytes(b"video")
            output_dir = directory / "out"
            output_dir.mkdir()
            output_path = output_dir / "one_RU.mp4"
            output_path.write_bytes(b"verified-video")
            state_path = directory / "batch.json"
            state = vt.create_batch_recovery_state(
                [str(input_path)], str(output_dir), {"voice": "voice", "model": "small"}
            )
            state["files"][0].update({"status": "processing", "output_path": str(output_path)})
            app = self._worker_app()

            with mock.patch.object(vt, "VideoTranslator") as translator_class, \
                    mock.patch.object(vt, "get_batch_recovery_state_path", return_value=state_path), \
                    mock.patch.object(vt, "find_ffmpeg", return_value="ffmpeg"), \
                    mock.patch.object(vt, "find_ffprobe", return_value="ffprobe"), \
                    mock.patch.object(vt, "output_has_video_and_audio", return_value=True):
                app._worker(
                    state,
                    str(output_dir),
                    "voice",
                    "small",
                    False,
                    0,
                    vt.get_target_language("Русский"),
                    False,
                    vt.normalize_audio_settings(),
                )

            translator_class.assert_not_called()
            saved = vt.load_batch_recovery_state(state_path)
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(saved["files"][0]["status"], "succeeded")


class TranslationBatchTests(unittest.TestCase):
    def test_batch_parser_requires_every_separator_in_order(self):
        _payload, markers = vt.build_translation_batch([(1, "Hello"), (2, "World")])
        valid = (
            f"{markers[0][1]}\nПривет\n{markers[0][2]}\n"
            f"{markers[1][1]}\nМир\n{markers[1][2]}"
        )
        self.assertEqual(vt.parse_translation_batch(valid, markers), {1: "Привет", 2: "Мир"})

        broken = valid.replace(markers[1][2], "")
        with self.assertRaises(ValueError):
            vt.parse_translation_batch(broken, markers)

    def test_invalid_batch_falls_back_to_individual_translation(self):
        translator = vt.VideoTranslator(lambda _message: None, cancel_event=threading.Event())
        records = [
            (1, {"source": "Hello"}),
            (2, {"source": "World"}),
        ]

        def fake_translate(text, index=0, attempts=3):
            if isinstance(index, str):
                return "ответ без разделителей"
            return {1: "Привет", 2: "Мир"}[index]

        translator.translate_segment = fake_translate
        result, failures = translator.translate_records_batch(records)
        self.assertEqual(result, {1: "Привет", 2: "Мир"})
        self.assertEqual(failures, [])


class NetworkAndCacheTests(unittest.TestCase):
    def test_progressive_retry_delay_is_capped_without_large_integer_overflow(self):
        self.assertEqual(vt.progressive_retry_delay(1, base=1.0, maximum=8.0), 1.0)
        self.assertEqual(vt.progressive_retry_delay(4, base=1.0, maximum=8.0), 8.0)
        self.assertEqual(vt.progressive_retry_delay(10_000, base=1.0, maximum=8.0), 8.0)

    def test_tts_recovery_sleeps_only_after_the_current_failed_segment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            translator = vt.VideoTranslator(lambda _message: None, cancel_event=threading.Event())
            translator.temp_dir = str(directory)
            translator.ffmpeg = "ffmpeg"
            translator.tts_cache = vt.TTSCache(directory / "cache")
            translator._sleep_or_cancel = mock.Mock()
            translator._await_selected_voice_or_fallback = mock.Mock(return_value="edge_recovered")
            translator._mix_in_batches = mock.Mock(return_value=str(directory / "mixed.wav"))

            call_counts = {}
            call_lock = threading.Lock()

            def fake_prepare(_text, _voice, index, _target_slot, _hard_slot):
                with call_lock:
                    call_counts[index] = call_counts.get(index, 0) + 1
                    call_no = call_counts[index]
                if call_no == 1 or (index == 1 and call_no == 2):
                    return None, 0.0, {}
                return str(directory / f"segment_{index}.wav"), 0.5, {
                    "native_rate": 0,
                    "tempo": 1.0,
                    "tempo_method": "",
                    "total_speed": 1.0,
                }

            translator._prepare_tts_segment = fake_prepare
            segments = [
                {
                    "start": float(index),
                    "end": float(index) + 0.8,
                    "translated": f"Фраза {index}",
                }
                for index in range(5)
            ]

            with mock.patch.object(vt, "master_voice_audio", return_value=str(directory / "master.wav")):
                result, _pause_plan, _duration = translator.build_timeline(
                    segments,
                    video_dur=10.0,
                    voice="ru-RU-DmitryNeural",
                )

            self.assertEqual(result, str(directory / "master.wav"))
            self.assertEqual(translator._sleep_or_cancel.call_count, 1)

    def test_network_storm_pauses_once_and_reports_recovery(self):
        now = [100.0]
        events = []
        sleeps = []
        guard = vt.NetworkStormGuard(
            event_cb=lambda event, **details: events.append((event, details)),
            clock=lambda: now[0],
        )
        for _ in range(vt.NETWORK_STORM_THRESHOLD):
            guard.record_failure("translation", TimeoutError("timed out"))

        def fake_sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        guard.before_request(fake_sleep)
        for _ in range(vt.NETWORK_STORM_RECOVERY_SUCCESSES):
            guard.record_success("translation")

        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], 30)
        self.assertEqual([event for event, _details in events], [
            "network_storm_started", "network_storm_finished",
        ])

    def test_tts_cache_survives_new_instance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "source.mp3"
            restored = directory / "restored.mp3"
            source.write_bytes(b"audio" * 100)
            key = vt.TTSCache.make_key("Текст", "voice", 15, "edge_tts", "ru")
            vt.TTSCache(directory).store(key, str(source), {"provider": "edge_tts"})

            self.assertTrue(vt.TTSCache(directory).restore(key, str(restored)))
            self.assertEqual(restored.read_bytes(), source.read_bytes())

    def test_success_cleanup_removes_only_entries_used_by_current_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "source.mp3"
            source.write_bytes(b"audio" * 100)
            first_key = vt.TTSCache.make_key("Первый", "voice", 0, "edge_tts", "ru")
            second_key = vt.TTSCache.make_key("Второй", "voice", 0, "edge_tts", "ru")
            writer = vt.TTSCache(directory)
            writer.store(first_key, str(source), {"provider": "edge_tts"})
            writer.store(second_key, str(source), {"provider": "edge_tts"})

            current_video_cache = vt.TTSCache(directory)
            restored = directory / "restored.mp3"
            self.assertTrue(current_video_cache.restore(first_key, str(restored)))
            result = current_video_cache.release_used_entries()

            self.assertEqual(result["deleted_entries"], 1)
            self.assertFalse((directory / f"{first_key}.mp3").exists())
            self.assertFalse((directory / f"{first_key}.json").exists())
            self.assertTrue((directory / f"{second_key}.mp3").exists())
            self.assertTrue((directory / f"{second_key}.json").exists())

    def test_stopped_cleanup_removes_prepared_wav_but_keeps_raw_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "source.audio"
            source.write_bytes(b"audio" * 100)
            raw_key = vt.TTSCache.make_key("Текст", "voice", 0, "edge_tts", "ru")
            prepared_key = vt.TTSCache.make_key(
                "Текст", "voice", 0, "prepared_wav_edge_tts", "ru"
            )
            cache = vt.TTSCache(directory)
            cache.store(raw_key, str(source), {"provider": "edge_tts"})
            cache.store(
                prepared_key,
                str(source),
                {"provider": "prepared_wav_edge_tts"},
                suffix=".wav",
            )

            result = cache.release_used_entries(suffixes={".wav"})

            self.assertEqual(result["deleted_entries"], 1)
            self.assertTrue((directory / f"{raw_key}.mp3").exists())
            self.assertTrue((directory / f"{raw_key}.json").exists())
            self.assertFalse((directory / f"{prepared_key}.wav").exists())
            self.assertFalse((directory / f"{prepared_key}.json").exists())

    def test_prepared_wav_expires_before_compact_recovery_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "source.audio"
            source.write_bytes(b"audio" * 100)
            raw_key = vt.TTSCache.make_key("Текст", "voice", 0, "edge_tts", "ru")
            prepared_key = vt.TTSCache.make_key(
                "Текст", "voice", 0, "prepared_wav_edge_tts", "ru"
            )
            writer = vt.TTSCache(directory)
            writer.store(raw_key, str(source), {"provider": "edge_tts"})
            writer.store(
                prepared_key,
                str(source),
                {"provider": "prepared_wav_edge_tts"},
                suffix=".wav",
            )
            old_time = time.time() - 2 * 86400
            for key, suffix in ((raw_key, ".mp3"), (prepared_key, ".wav")):
                for path in (directory / f"{key}{suffix}", directory / f"{key}.json"):
                    os.utime(path, (old_time, old_time))

            result = vt.TTSCache(directory).cleanup_stale_entries(
                retention_days=7,
                prepared_retention_hours=24,
                orphan_hours=24,
                max_bytes=1024 ** 3,
                target_bytes=512 * 1024 ** 2,
            )

            self.assertEqual(result["prepared_entries"], 1)
            self.assertTrue((directory / f"{raw_key}.mp3").exists())
            self.assertFalse((directory / f"{prepared_key}.wav").exists())

    def test_size_limit_removes_prepared_wav_before_older_raw_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            cache_directory = directory / "cache"
            raw_source = directory / "raw.audio"
            prepared_source = directory / "prepared.audio"
            raw_source.write_bytes(b"r" * 500)
            prepared_source.write_bytes(b"w" * 2500)
            raw_key = vt.TTSCache.make_key("Старый MP3", "voice", 0, "edge_tts", "ru")
            prepared_key = vt.TTSCache.make_key(
                "Новый WAV", "voice", 0, "prepared_wav_edge_tts", "ru"
            )
            writer = vt.TTSCache(cache_directory)
            writer.store(raw_key, str(raw_source), {"provider": "edge_tts"})
            writer.store(
                prepared_key,
                str(prepared_source),
                {"provider": "prepared_wav_edge_tts"},
                suffix=".wav",
            )
            older_time = time.time() - 3600
            for path in (
                cache_directory / f"{raw_key}.mp3",
                cache_directory / f"{raw_key}.json",
            ):
                os.utime(path, (older_time, older_time))

            result = vt.TTSCache(cache_directory).cleanup_stale_entries(
                retention_days=7,
                prepared_retention_hours=24,
                orphan_hours=24,
                max_bytes=2500,
                target_bytes=1000,
            )

            self.assertEqual(result["size_limited_entries"], 1)
            self.assertTrue((cache_directory / f"{raw_key}.mp3").exists())
            self.assertFalse((cache_directory / f"{prepared_key}.wav").exists())

    def test_store_does_not_add_prepared_wav_beyond_hard_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            cache_directory = directory / "cache"
            source = directory / "source.audio"
            source.write_bytes(b"audio" * 100)
            prepared_key = vt.TTSCache.make_key(
                "WAV", "voice", 0, "prepared_wav_edge_tts", "ru"
            )
            raw_key = vt.TTSCache.make_key("MP3", "voice", 0, "edge_tts", "ru")
            cache = vt.TTSCache(cache_directory, max_bytes=400)

            prepared_stored = cache.store(
                prepared_key,
                str(source),
                {"provider": "prepared_wav_edge_tts"},
                suffix=".wav",
            )
            raw_stored = cache.store(raw_key, str(source), {"provider": "edge_tts"})

            self.assertFalse(prepared_stored)
            self.assertFalse((cache_directory / f"{prepared_key}.wav").exists())
            self.assertTrue(raw_stored)
            self.assertTrue((cache_directory / f"{raw_key}.mp3").exists())

    def test_stale_cleanup_keeps_recent_recovery_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "source.mp3"
            source.write_bytes(b"audio" * 100)
            stale_key = vt.TTSCache.make_key("Старый", "voice", 0, "edge_tts", "ru")
            fresh_key = vt.TTSCache.make_key("Свежий", "voice", 0, "edge_tts", "ru")
            writer = vt.TTSCache(directory)
            writer.store(stale_key, str(source), {"provider": "edge_tts"})
            writer.store(fresh_key, str(source), {"provider": "edge_tts"})
            unrelated_audio = directory / "не_кэш_пользователя.wav"
            unrelated_audio.write_bytes(b"user-audio" * 50)
            old_time = time.time() - 30 * 86400
            for suffix in (".mp3", ".json"):
                path = directory / f"{stale_key}{suffix}"
                path.touch()
                os.utime(path, (old_time, old_time))
            os.utime(unrelated_audio, (old_time, old_time))

            result = vt.TTSCache(directory).cleanup_stale_entries(
                retention_days=14,
                orphan_hours=24,
                max_bytes=1024 ** 3,
                target_bytes=512 * 1024 ** 2,
            )

            self.assertEqual(result["stale_entries"], 1)
            self.assertFalse((directory / f"{stale_key}.mp3").exists())
            self.assertTrue((directory / f"{fresh_key}.mp3").exists())
            self.assertTrue(unrelated_audio.exists())

    def test_edge_storm_uses_bypass_and_only_ends_after_spaced_probes(self):
        now = [100.0]
        events = []
        guard = vt.NetworkStormGuard(
            event_cb=lambda event, **details: events.append((event, details)),
            clock=lambda: now[0],
            service="edge_tts",
            threshold=2,
            window_sec=120,
            initial_pause_sec=60,
            recovery_successes=3,
            recovery_min_sec=30,
            probe_interval_sec=15,
        )
        guard.record_failure("edge_tts", TimeoutError("one"))
        guard.record_failure("edge_tts", TimeoutError("two"))
        self.assertTrue(guard.is_active())
        self.assertEqual(guard.claim_probe_or_bypass(), "bypass")

        now[0] += 60
        self.assertEqual(guard.claim_probe_or_bypass(), "probe")
        guard.record_success("edge_tts")
        self.assertEqual(guard.claim_probe_or_bypass(), "bypass")
        now[0] += 15
        self.assertEqual(guard.claim_probe_or_bypass(), "probe")
        guard.record_success("edge_tts")
        now[0] += 15
        self.assertEqual(guard.claim_probe_or_bypass(), "probe")
        guard.record_success("edge_tts")

        self.assertFalse(guard.is_active())
        self.assertEqual([event for event, _details in events], [
            "network_storm_started", "network_storm_finished",
        ])

    def test_wav_duration_does_not_start_ffprobe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "one_second.wav"
            with wave.open(str(path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(8000)
                wav_file.writeframes(b"\x00\x00" * 8000)
            with mock.patch.object(vt, "run_subprocess", side_effect=AssertionError("ffprobe called")):
                self.assertAlmostEqual(vt.get_audio_duration("ffprobe", str(path)), 1.0, places=3)

    def test_native_rate_failure_does_not_call_rate_ignorant_gtts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            translator = vt.VideoTranslator(lambda _message: None, cancel_event=threading.Event())
            translator.tts_cache = vt.TTSCache(Path(temp_dir))
            translator._run_edge_tts = mock.Mock(side_effect=TimeoutError("vpn timeout"))
            translator._sleep_or_cancel = mock.Mock()
            forbidden_gtts = types.SimpleNamespace(
                gTTS=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("gTTS must not be called"))
            )
            with mock.patch.dict(sys.modules, {"gtts": forbidden_gtts}):
                result = translator.generate_tts(
                    "Тестовая фраза", str(Path(temp_dir) / "rate.mp3"), "voice", rate_pct=20,
                    segment_index=7,
                )

            self.assertFalse(result)
            self.assertEqual(translator._run_edge_tts.call_count, 3)

    def test_edge_storm_waits_for_selected_voice_before_gtts_fallback(self):
        # Historical test name is kept because task routing selects it directly.
        # Current contract: no VPN popup/wait; recovery enables gTTS automatically.
        with tempfile.TemporaryDirectory() as temp_dir:
            notices = []
            translator = vt.VideoTranslator(
                lambda _message: None,
                cancel_event=threading.Event(),
                network_notice_cb=lambda action, details: notices.append((action, details)),
            )
            translator.temp_dir = temp_dir
            translator.tts_cache = vt.TTSCache(Path(temp_dir))
            translator._sleep_or_cancel = mock.Mock(side_effect=AssertionError("VPN wait must not run"))
            translator._run_edge_tts = mock.Mock(side_effect=AssertionError("Edge must be bypassed"))
            for _ in range(5):
                translator.edge_network_guard.record_failure("edge_tts", TimeoutError("vpn timeout"))

            class FakeGTTS:
                def __init__(self, **_kwargs):
                    pass

                def save(self, path):
                    Path(path).write_bytes(b"gtts-audio" * 50)

            with mock.patch.dict(sys.modules, {"gtts": types.SimpleNamespace(gTTS=FakeGTTS)}):
                deferred = translator.generate_tts(
                    "Обычная фраза", str(Path(temp_dir) / "normal.mp3"), "voice",
                    rate_pct=0, segment_index=8,
                )
                decision = translator._await_selected_voice_or_fallback(
                    "Обычная фраза", "voice", 8
                )
                result = translator.generate_tts(
                    "Обычная фраза", str(Path(temp_dir) / "fallback.mp3"), "voice",
                    rate_pct=0, segment_index=8,
                )

            self.assertFalse(deferred)
            self.assertEqual(decision, "gtts_allowed")
            self.assertEqual(result, "gtts")
            translator._run_edge_tts.assert_not_called()
            translator._sleep_or_cancel.assert_not_called()
            self.assertTrue(translator.edge_network_guard.is_active())
            self.assertEqual(translator.tts_stats["gtts_fallback"], 1)
            self.assertEqual(notices, [])
            self.assertEqual(translator._tts_fallback_reason, "automatic_edge_unavailable")

    def test_manual_vpn_probe_opens_storm_for_immediate_check(self):
        guard = vt.NetworkStormGuard(service="edge_tts", threshold=2, initial_pause_sec=30)
        guard.record_failure("edge_tts", TimeoutError("one"))
        guard.record_failure("edge_tts", TimeoutError("two"))

        self.assertEqual(guard.claim_probe_or_bypass(), "bypass")
        self.assertTrue(guard.request_probe_now())
        self.assertEqual(guard.claim_probe_or_bypass(), "probe")

    def test_user_can_authorize_fallback_without_waiting_for_deadline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            translator = vt.VideoTranslator(lambda _message: None, cancel_event=threading.Event())
            translator.temp_dir = temp_dir
            translator.selected_voice = "ru-RU-DmitryNeural"
            translator._begin_edge_voice_preservation("test")
            self.assertTrue(translator.allow_gtts_fallback_now("test_user"))

            decision = translator._await_selected_voice_or_fallback(
                "Проверочная фраза",
                translator.selected_voice,
                11,
            )

            self.assertEqual(decision, "gtts_allowed")
            self.assertTrue(translator._tts_allow_gtts_fallback)
            self.assertEqual(translator._tts_fallback_reason, "test_user")

    def test_successful_manual_vpn_probe_restores_selected_edge_voice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            notices = []
            translator = vt.VideoTranslator(
                lambda _message: None,
                cancel_event=threading.Event(),
                network_notice_cb=lambda action, details: notices.append((action, details)),
            )
            translator.temp_dir = temp_dir
            translator.tts_cache = vt.TTSCache(Path(temp_dir) / "cache")
            translator.selected_voice = "ru-RU-DmitryNeural"
            translator._run_edge_tts = lambda _text, path, _voice, rate="+0%": Path(path).write_bytes(
                b"edge-audio" * 50
            )
            for _ in range(3):
                translator.edge_network_guard.record_failure("edge_tts", TimeoutError("vpn timeout"))
            self.assertTrue(translator.request_edge_probe_now())

            decision = translator._await_selected_voice_or_fallback(
                "Проверочная фраза",
                translator.selected_voice,
                12,
            )

            self.assertEqual(decision, "edge_recovered")
            self.assertFalse(translator.edge_network_guard.is_active())
            self.assertFalse(translator._tts_allow_gtts_fallback)
            self.assertIn("recovered", [action for action, _details in notices])

    def test_prepared_tts_cache_separates_edge_and_gtts(self):
        common = {"audio_settings": {"voice_volume_pct": 100}, "source_provider": "edge_tts"}
        edge_key = vt.TTSCache.make_key(
            "Одна фраза", "ru-RU-DmitryNeural", 0, "prepared_wav_edge_tts", "ru",
            extra=common,
        )
        gtts_key = vt.TTSCache.make_key(
            "Одна фраза", "ru-RU-DmitryNeural", 0, "prepared_wav_gtts", "ru",
            extra={**common, "source_provider": "gtts"},
        )

        self.assertNotEqual(edge_key, gtts_key)

    def test_reusable_translator_uses_one_session(self):
        sessions = []
        google_module = types.SimpleNamespace()

        class FakeResponse:
            text = "ok"

        class FakeSession:
            def __init__(self):
                self.calls = 0
                self.closed = False
                sessions.append(self)

            def mount(self, *_args):
                pass

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return FakeResponse()

            def close(self):
                self.closed = True

        class FakeGoogleTranslator:
            def __init__(self, **_kwargs):
                pass

            def translate(self, text):
                return google_module.requests.get("https://translate.test", params={"q": text}).text

        fake_requests = types.SimpleNamespace(
            Session=FakeSession,
            adapters=types.SimpleNamespace(HTTPAdapter=lambda **_kwargs: object()),
        )
        google_module.requests = fake_requests
        fake_deep = types.SimpleNamespace(GoogleTranslator=FakeGoogleTranslator)

        with mock.patch.dict(sys.modules, {
            "requests": fake_requests,
            "deep_translator": fake_deep,
            "deep_translator.google": google_module,
        }):
            client = vt.ReusableGoogleTranslator("ru")
            self.assertEqual(client.translate("one"), "ok")
            self.assertEqual(client.translate("two"), "ok")
            client.close()

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].calls, 2)
        self.assertTrue(sessions[0].closed)


class PausePlanTests(unittest.TestCase):
    def test_nearby_pauses_merge_only_without_speech_boundary(self):
        pauses = [
            {"at": 2.00, "duration": 0.4, "segments": [1]},
            {"at": 2.20, "duration": 0.5, "segments": [1]},
            {"at": 4.00, "duration": 0.3, "segments": [2]},
        ]
        segments = [
            {"start": 0.0, "end": 1.8},
            {"start": 3.0, "end": 3.8},
        ]
        merged = vt.merge_nearby_pause_plan(pauses, 10.0, segments)
        self.assertEqual(len(merged), 2)
        self.assertAlmostEqual(merged[0]["duration"], 0.9)


class TTSParallelTests(unittest.TestCase):
    def test_parallel_preparation_keeps_timeline_order(self):
        translator = vt.VideoTranslator(lambda _message: None, cancel_event=threading.Event())
        translator.speech_speed_limit = 1.20
        active = 0
        max_active = 0
        active_lock = threading.Lock()
        captured = []

        def fake_prepare(_text, _voice, index, _target_slot, _hard_slot):
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.01 * (4 - index))
            with active_lock:
                active -= 1
            return f"segment-{index}.wav", 0.5, {"total_speed": 1.0, "tempo": 1.0}

        def fake_mix(segments, _duration):
            captured.extend(segments)
            return "mixed.wav"

        segments = [
            {"start": float(index - 1), "end": float(index), "source": f"s{index}", "translated": f"t{index}"}
            for index in range(1, 4)
        ]
        translator._prepare_tts_segment = fake_prepare
        translator._mix_in_batches = fake_mix

        with mock.patch.object(vt, "master_voice_audio", return_value="master.wav"):
            result, pauses, duration = translator.build_timeline(segments, 4.0, "voice")

        self.assertEqual(result, "master.wav")
        self.assertEqual(pauses, [])
        self.assertEqual(duration, 4.0)
        self.assertGreaterEqual(max_active, 2)
        self.assertLessEqual(max_active, vt.TTS_PREPARE_WORKERS)
        self.assertEqual([path for _delay, path in captured], [
            "segment-1.wav", "segment-2.wav", "segment-3.wav",
        ])

    def test_failed_parallel_segment_is_recovered_sequentially(self):
        translator = vt.VideoTranslator(lambda _message: None, cancel_event=threading.Event())
        calls = {}
        calls_lock = threading.Lock()
        captured = []

        def fake_prepare(_text, _voice, index, _target_slot, _hard_slot):
            with calls_lock:
                calls[index] = calls.get(index, 0) + 1
                call_number = calls[index]
            if index == 2 and call_number == 1:
                return None, 0.0, {}
            return f"segment-{index}.wav", 0.5, {"total_speed": 1.0, "tempo": 1.0}

        def fake_mix(segments, _duration):
            captured.extend(segments)
            return "mixed.wav"

        segments = [
            {"start": float(index - 1), "end": float(index), "source": f"s{index}", "translated": f"t{index}"}
            for index in range(1, 4)
        ]
        translator._prepare_tts_segment = fake_prepare
        translator._mix_in_batches = fake_mix
        translator._await_selected_voice_or_fallback = mock.Mock(return_value="edge_recovered")

        with mock.patch.object(vt, "master_voice_audio", return_value="master.wav"):
            result, pauses, _duration = translator.build_timeline(segments, 4.0, "voice")

        self.assertEqual(result, "master.wav")
        self.assertEqual(pauses, [])
        self.assertEqual(calls[2], 2)
        self.assertEqual(translator.tts_stats["recovered_on_second_pass"], 1)
        self.assertEqual(translator.tts_stats["failed"], 0)
        self.assertEqual([path for _delay, path in captured], [
            "segment-1.wav", "segment-2.wav", "segment-3.wav",
        ])

    def test_final_provider_accounting_removes_temporary_gtts_when_edge_wins(self):
        translator = vt.VideoTranslator(lambda _message: None, cancel_event=threading.Event())
        translator.speech_speed_limit = 1.25
        translator._mark_gtts_fallback(1)
        translator._get_prepared_tts_audio = mock.Mock(side_effect=[
            ("base-gtts.wav", "gtts"),
            ("fast-edge.wav", "edge_tts"),
        ])

        with mock.patch("videotranslator.pipeline.tts_prepare.get_audio_duration", side_effect=[2.0, 1.4]), \
             mock.patch("videotranslator.pipeline.tts_prepare.edge_rate_from_speed", return_value=25):
            path, _duration, stats = translator._prepare_tts_segment("text", "voice", 1, 1.5, 1.5)

        self.assertEqual(path, "fast-edge.wav")
        self.assertEqual(stats["source_provider"], "edge_tts")
        self.assertEqual(translator.tts_stats["gtts_fallback"], 0)

    def test_final_voice_repair_replaces_gtts_segment_after_successful_probe(self):
        translator = vt.VideoTranslator(lambda _message: None, cancel_event=threading.Event())
        translator.speech_speed_limit = 1.20
        calls = {}
        captured = []

        def fake_prepare(_text, _voice, index, _target_slot, _hard_slot):
            calls[index] = calls.get(index, 0) + 1
            if index == 2 and calls[index] == 1:
                translator._set_final_tts_provider(index, "gtts")
                return "segment-2-gtts.wav", 0.5, {"total_speed": 1.0, "tempo": 1.0, "source_provider": "gtts"}
            translator._set_final_tts_provider(index, "edge_tts")
            return f"segment-{index}-edge.wav", 0.5, {"total_speed": 1.0, "tempo": 1.0, "source_provider": "edge_tts"}

        translator._prepare_tts_segment = fake_prepare
        translator._probe_edge_voice_once = mock.Mock(return_value=True)
        translator._mix_in_batches = lambda segments, _duration: captured.extend(segments) or "mixed.wav"
        segments = [
            {"start": 0.0, "end": 1.0, "source": "s1", "translated": "t1"},
            {"start": 1.0, "end": 2.0, "source": "s2", "translated": "t2"},
        ]

        with mock.patch.object(vt, "master_voice_audio", return_value="master.wav"):
            result, _pauses, _duration = translator.build_timeline(segments, 3.0, "voice")

        self.assertEqual(result, "master.wav")
        self.assertEqual(calls[2], 2)
        self.assertEqual(translator.tts_stats["gtts_fallback"], 0)
        self.assertEqual(translator.tts_stats["gtts_repaired_to_edge"], 1)
        self.assertIn((1000, "segment-2-edge.wav"), captured)
        translator._probe_edge_voice_once.assert_called_once()

    def test_final_voice_repair_keeps_gtts_without_wait_when_probe_fails(self):
        translator = vt.VideoTranslator(lambda _message: None, cancel_event=threading.Event())
        calls = {}
        captured = []

        def fake_prepare(_text, _voice, index, _target_slot, _hard_slot):
            calls[index] = calls.get(index, 0) + 1
            if index == 2:
                translator._set_final_tts_provider(index, "gtts")
                return "segment-2-gtts.wav", 0.5, {"total_speed": 1.0, "tempo": 1.0, "source_provider": "gtts"}
            translator._set_final_tts_provider(index, "edge_tts")
            return "segment-1-edge.wav", 0.5, {"total_speed": 1.0, "tempo": 1.0, "source_provider": "edge_tts"}

        translator._prepare_tts_segment = fake_prepare
        translator._probe_edge_voice_once = mock.Mock(return_value=False)
        translator._mix_in_batches = lambda segments, _duration: captured.extend(segments) or "mixed.wav"
        segments = [
            {"start": 0.0, "end": 1.0, "source": "s1", "translated": "t1"},
            {"start": 1.0, "end": 2.0, "source": "s2", "translated": "t2"},
        ]

        with mock.patch.object(vt, "master_voice_audio", return_value="master.wav"):
            translator.build_timeline(segments, 3.0, "voice")

        self.assertEqual(calls[2], 1)
        self.assertEqual(translator.tts_stats["gtts_fallback"], 1)
        self.assertFalse(translator.tts_stats["voice_repair_probe_success"])
        self.assertIn((1000, "segment-2-gtts.wav"), captured)

    def test_speed_limit_is_normalized_to_supported_value(self):
        self.assertEqual(vt.normalize_audio_settings({"speech_speed_limit": 1.19})["speech_speed_limit"], 1.20)


if __name__ == "__main__":
    unittest.main()
