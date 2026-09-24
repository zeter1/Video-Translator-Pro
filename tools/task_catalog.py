from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


# Source of truth for Codex routing. Keep cards deterministic and narrow.
# The runtime application does not import this file.
TASK_CARDS = [
    {
        "id": "startup_recovery_offer",
        "title": "Startup batch recovery offer",
        "aliases": [
            "startup recovery previous session restore dialog",
            "startup batch recovery",
            "restore previous batch on startup",
            "восстановление прошлого запуска при старте",
            "предложение восстановить прошлую очередь",
        ],
        "keywords": ["startup", "recovery", "previous", "session", "restore", "dialog", "старт", "восстанов", "прошл", "сесс"],
        "primary": "videotranslator/ui/batch_recovery.py",
        "support": ["videotranslator/recovery/batch.py"],
        "symbols": ["UIBatchRecoveryMixin._offer_batch_recovery", "UIBatchRecoveryMixin._restore_batch_settings"],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "BatchRecoveryTests.test_offer_batch_recovery_with_empty_previous_problem_log_is_safe",
            "BatchRecoveryTests.test_old_problem_log_reconstructs_unfinished_batch",
        ],
        "avoid": ["videotranslator/pipeline/", "videotranslator/tts/", "videotranslator/media/final_video.py"],
        "default_lines": 100,
    },
    {
        "id": "persistent_task_queue",
        "title": "Persistent translation task queue / Tasks tab / restart resume",
        "aliases": [
            "persistent translation task queue tasks tab restart resume fifo",
            "add translation while another task is running",
            "tasks tab notification completion",
            "очередь задач переводов после перезапуска",
            "вкладка задачи продолжить перевод",
            "добавить несколько задач по порядку",
        ],
        "keywords": ["task", "queue", "fifo", "persistent", "restart", "resume", "notification", "задач", "очеред", "перезапуск", "продолж", "уведом"],
        "primary": "videotranslator/recovery/tasks.py",
        "support": [
            "videotranslator/ui/tasks.py",
            "videotranslator/ui/batch_start.py",
            "videotranslator/ui/batch_worker.py",
            "videotranslator/ui/batch_recovery.py",
            "videotranslator/ui/controls.py",
            "videotranslator/ui/settings.py",
            "videotranslator/core/instance_lock.py",
            "videotranslator/bootstrap.py",
        ],
        "symbols": [
            "create_translation_task",
            "load_task_queue_state",
            "save_task_queue_state",
            "recover_interrupted_tasks",
            "UITasksMixin._dispatch_next_task",
            "UITasksMixin._on_task_batch_finished",
        ],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "DurableTaskQueueTests.test_fifo_round_trip_and_next_task_order",
            "DurableTaskQueueTests.test_running_task_becomes_resumable_after_restart",
            "QueueModeStartTests.test_start_enqueues_snapshot_while_another_task_is_processing",
            "DurableQueueRecoveryPass11Tests.test_valid_json_with_non_object_root_is_preserved_and_reset",
            "DurableQueueRecoveryPass11Tests.test_valid_object_with_non_array_tasks_is_backed_up_instead_of_silently_dropped",
            "DurableQueueRecoveryPass11Tests.test_malformed_runtime_numbers_are_normalized_for_tasks_ui",
            "DurableQueueRecoveryPass11Tests.test_completed_batch_left_running_is_finalized_without_reprocessing",
            "DurableQueueRecoveryPass11Tests.test_load_repairs_task_and_batch_identity_mismatch",
            "QueuePersistenceTransactionsPass11Tests.test_enqueue_rolls_back_if_durable_write_fails",
            "QueuePersistenceTransactionsPass11Tests.test_failed_transaction_preserves_preexisting_dirty_state",
            "QueuePersistenceTransactionsPass11Tests.test_dispatch_does_not_launch_uncommitted_running_transition",
            "QueuePersistenceTransactionsPass11Tests.test_worker_checkpoint_updates_its_batch_not_new_active_task",
            "QueueGenerationGuardPass11Tests.test_current_success_finalizes_task_notifies_and_advances_fifo",
            "QueueGenerationGuardPass11Tests.test_late_completion_cannot_release_newer_active_task",
            "WorkerLifecyclePass11Tests.test_queue_worker_does_not_release_busy_before_task_finalizer",
            "LegacyMigrationPass11Tests.test_removed_migrated_legacy_task_does_not_resurrect",
            "LegacyRecoveryDialogPass11Tests.test_known_removed_task_is_not_offered_by_legacy_recovery_dialog",
            "LegacyBatchRecoveryHardeningPass11Tests.test_non_object_legacy_recovery_json_is_ignored_without_startup_crash",
            "LegacyBatchRecoveryHardeningPass11Tests.test_malformed_legacy_file_entries_are_ignored_without_pending_entry_crash",
            "InvalidSavedSettingsPass11Tests.test_corrupt_saved_settings_fail_cleanly_before_thread_start",
            "DurableQueuePass12Tests.test_persisted_cancel_request_does_not_resume_after_restart",
            "DurableQueuePass12Tests.test_semantically_broken_task_is_backed_up_before_valid_subset_is_published",
            "BatchWorkerPass12Tests.test_missing_previously_succeeded_output_is_processed_again",
            "BatchWorkerPass12Tests.test_changed_source_invalidates_previously_succeeded_output",
            "QueueProgressGenerationPass12Tests.test_late_progress_callback_from_previous_task_cannot_reset_new_task",
            "DirtyQueueGatePass12Tests.test_dirty_terminal_state_must_persist_before_next_task_can_launch",
            "ShutdownQueuePass12Tests.test_close_waits_for_live_worker_even_after_busy_flag_was_cleared",
            "SingleInstanceQueuePass12Tests.test_second_process_lock_owner_is_rejected_and_release_allows_reacquire",
            "TaskActionLockPass12Tests.test_running_remove_dialog_is_shown_after_queue_lock_is_released",
            "CancelIntentPass12Tests.test_cancel_button_persists_cancellation_intent_before_worker_finishes",
            "SavedSettingsOverflowPass12Tests.test_infinite_saved_original_volume_fails_cleanly_before_thread_start",
        ],
        "avoid": ["videotranslator/pipeline/timeline_", "videotranslator/media/final_video.py"],
        "default_lines": 180,
    },
    {
        "id": "batch_recovery_state",
        "title": "Batch recovery persisted state / problem-log reconstruction",
        "aliases": [
            "batch recovery state problem log reconstruct empty path",
            "reconstruct batch from problem log",
            "batch recovery empty path",
            "latest batch recovery json",
            "восстановление пакетной очереди по логу",
            "пустой путь problem log",
        ],
        "keywords": ["batch", "recovery", "state", "problem", "log", "reconstruct", "empty", "path", "queue", "восстанов", "пакет", "лог", "пуст", "путь", "очеред"],
        "primary": "videotranslator/recovery/batch.py",
        "support": ["videotranslator/ui/batch_recovery.py"],
        "symbols": [
            "reconstruct_batch_recovery_state_from_problem_log",
            "load_batch_recovery_state",
            "save_batch_recovery_state",
            "batch_recovery_pending_entries",
        ],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "BatchRecoveryTests.test_batch_state_round_trip_keeps_completed_files_out_of_pending",
            "BatchRecoveryTests.test_old_problem_log_recovery_ignores_empty_or_directory_path",
            "BatchRecoveryTests.test_old_problem_log_reconstructs_unfinished_batch",
        ],
        "avoid": ["videotranslator/pipeline/process.py", "videotranslator/pipeline/timeline_build.py", "videotranslator/media/"],
        "default_lines": 120,
    },
    {
        "id": "batch_start",
        "title": "Batch start / validation / output directory",
        "aliases": [
            "batch start validation output folder",
            "start batch button validation",
            "начать обработку очередь папка результата",
            "запуск пакетной обработки",
        ],
        "keywords": ["batch", "start", "validation", "output", "folder", "button", "запуск", "обработ", "очеред", "папк", "результ"],
        "primary": "videotranslator/ui/batch_start.py",
        "support": ["videotranslator/recovery/batch.py", "videotranslator/ui/queue.py"],
        "symbols": ["UIBatchStartMixin.start", "UIBatchStartMixin._start_batch_worker"],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "BatchStartLifecycleTests.test_reentrant_start_returns_before_touching_queue",
            "BatchStartLifecycleTests.test_worker_start_failure_restores_idle_state_and_marks_recovery",
        ],
        "avoid": ["videotranslator/pipeline/timeline_", "videotranslator/diagnostics/problem_analysis.py"],
        "default_lines": 120,
    },
    {
        "id": "batch_worker",
        "title": "Batch worker / multiple files / progress",
        "aliases": [
            "batch worker multiple files progress state",
            "batch file loop",
            "пакетный worker несколько видео прогресс",
            "обработка очереди видео",
        ],
        "keywords": ["batch", "worker", "multiple", "files", "progress", "loop", "очеред", "пакет", "нескольк", "прогресс"],
        "primary": "videotranslator/ui/batch_worker.py",
        "support": ["videotranslator/recovery/batch.py", "videotranslator/pipeline/translator.py"],
        "symbols": ["UIBatchWorkerMixin._worker"],
        "phases": ["BW1", "BW2", "BW3", "BW4"],
        "phase_routes": {
            "BW1": ["batch init initialize state settings"],
            "BW2": ["file loop next file skip existing"],
            "BW3": ["run video translator process"],
            "BW4": ["finalize batch finish cleanup"],
        },
        "tests": [
            "BatchRecoveryTests.test_worker_saves_successful_batch_state",
            "BatchRecoveryTests.test_worker_reuses_only_verified_existing_output",
        ],
        "avoid": ["videotranslator/pipeline/timeline_build.py", "videotranslator/pipeline/tts_generate.py"],
        "default_lines": 120,
    },
    {
        "id": "video_pipeline",
        "title": "One-video pipeline orchestration",
        "aliases": [
            "one video pipeline orchestration process stages",
            "video process workflow",
            "общий pipeline одного видео",
            "этапы обработки одного видео",
        ],
        "keywords": ["video", "pipeline", "process", "workflow", "stage", "orchestration", "видео", "обработ", "этап", "пайплайн"],
        "primary": "videotranslator/pipeline/process.py",
        "support": ["videotranslator/pipeline/translator.py"],
        "symbols": ["ProcessMixin.process"],
        "phases": ["VT1", "VT2", "VT3", "VT4", "VT5", "VT6", "VT7", "VT8"],
        "phase_routes": {
            "VT1": ["audio extract extraction"],
            "VT2": ["whisper transcription recognition"],
            "VT3": ["translation translate checkpoint retry"],
            "VT4": ["manual review editor"],
            "VT5": ["tts timeline voice"],
            "VT6": ["final mux mp4"],
            "VT7": ["reports success translated text"],
            "VT8": ["cleanup temp failure"],
        },
        "tests": [],
        "avoid": ["videotranslator/diagnostics/problem_analysis.py", "videotranslator/config_languages.py"],
        "default_lines": 140,
    },
    {
        "id": "whisper",
        "title": "Whisper transcription / GPU",
        "aliases": [
            "whisper transcription recognition model gpu cuda",
            "speech recognition whisper",
            "распознавание речи whisper gpu cuda",
            "модель whisper",
        ],
        "keywords": ["whisper", "transcription", "recognition", "model", "gpu", "cuda", "распозна", "реч", "модель"],
        "primary": "videotranslator/speech/whisper.py",
        "support": ["videotranslator/pipeline/process.py"],
        "symbols": ["get_whisper_model", "is_cuda_available"],
        "phases": ["VT2"],
        "phase_routes": {"VT2": ["whisper transcription recognition model gpu cuda"]},
        "tests": [],
        "avoid": ["videotranslator/pipeline/tts_", "videotranslator/ui/review_"],
        "default_lines": 90,
    },
    {
        "id": "local_ai_models",
        "title": "Local AI models / startup update / persistent voices",
        "aliases": [
            "local ai models startup update persistent voices",
            "model manager install update verify piper argos marian",
            "startup model maintenance checksum download resume",
            "локальные модели голоса обновление при запуске",
            "установка голоса один раз постоянное хранилище",
            "argos marian piper менеджер моделей",
        ],
        "keywords": [
            "local", "ai", "model", "models", "startup", "update", "persistent", "voice",
            "piper", "argos", "marian", "install", "maintenance", "checksum", "download",
            "локальн", "модел", "голос", "обновлен", "установ", "хранилищ", "запуск",
        ],
        "primary": "videotranslator/models/manager_v8.py",
        "support": ["videotranslator/ui/models_tab.py", "videotranslator/models/catalog.py"],
        "symbols": [
            "RuntimeModelManager.startup_scan_installed_updates",
            "RuntimeModelManager.scan_installed_updates",
            "RuntimeModelManager._process_store_lock",
            "RuntimeModelManager._dir_size_mb",
            "RuntimeModelManager.install",
            "RuntimeModelManager._download",
            "UIModelsMixin.schedule_model_maintenance",
        ],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "ModelManagerReliabilityTests.test_startup_scan_is_quick_when_recent_full_integrity_check_exists",
            "ModelManagerReliabilityTests.test_startup_full_scan_marks_maintenance_only_when_healthy",
            "ModelManagerReliabilityTests.test_http_416_accepts_checksum_proven_complete_partial",
            "ModelManagerReliabilityTests.test_catalog_argos_status_requires_direct_package_not_only_pivot_route",
            "StartupUpdatePromptTests.test_user_can_decline_startup_update_without_any_download",
            "ModelStoreScanLockTests.test_public_scan_refuses_to_adopt_manifests_while_other_process_owns_store",
            "ModelStoreScanLockTests.test_startup_scan_refuses_to_advance_state_while_other_process_owns_store",
            "ModelUIThreadLifecycleTests.test_startup_thread_failure_releases_model_busy_gate",
        ],
        "avoid": ["videotranslator/media/", "videotranslator/diagnostics/problem_analysis.py", "videotranslator/ui/review_window.py"],
        "default_lines": 140,
    },
    {
        "id": "translation_checkpoint",
        "title": "Translation checkpoint / resume / integrity",
        "aliases": [
            "translation checkpoint resume incomplete text",
            "translation checkpoint retry",
            "checkpoint перевода продолжить",
            "незавершенный перевод checkpoint",
        ],
        "keywords": ["translation", "checkpoint", "resume", "incomplete", "integrity", "перевод", "чекпоинт", "продолж", "незаверш"],
        "primary": "videotranslator/recovery/translation.py",
        "support": ["videotranslator/pipeline/translation.py", "videotranslator/pipeline/process.py"],
        "symbols": [
            "translation_checkpoint_path",
            "load_translation_checkpoint",
            "save_translation_checkpoint",
        ],
        "phases": ["VT3A", "VT3D"],
        "phase_routes": {
            "VT3A": ["checkpoint plan resume translated records"],
            "VT3D": ["translation integrity incomplete validation"],
        },
        "tests": [
            "TranslationCheckpointTests.test_checkpoint_round_trip",
            "TranslationCheckpointTests.test_one_failed_segment_stops_incomplete_video_but_keeps_checkpoint",
        ],
        "avoid": ["videotranslator/pipeline/tts_", "videotranslator/media/final_video.py"],
        "default_lines": 110,
    },
    {
        "id": "translation_requests",
        "title": "Google translation / retries / per-segment translation",
        "aliases": [
            "translation google translate retry client",
            "google translator retry failed segment",
            "перевод google translate повтор ошибка сегмента",
        ],
        "keywords": ["translation", "google", "translate", "retry", "client", "segment", "перевод", "повтор", "ошиб", "сегмент"],
        "primary": "videotranslator/pipeline/translation.py",
        "support": ["videotranslator/network/guard.py", "videotranslator/recovery/translation.py"],
        "symbols": ["TranslationMixin.translate_segment", "TranslationMixin.translate_records_batch"],
        "phases": ["VT3B", "VT3C"],
        "phase_routes": {
            "VT3B": ["batch translate google request"],
            "VT3C": ["deferred retry failed segment"],
        },
        "tests": [
            "TranslationCheckpointTests.test_translation_retry_is_bounded_and_reported",
            "TranslationCheckpointTests.test_one_failed_segment_stops_incomplete_video_but_keeps_checkpoint",
        ],
        "avoid": ["videotranslator/pipeline/tts_prepare.py", "videotranslator/media/final_video.py"],
        "default_lines": 130,
    },
    {
        "id": "translation_batch_parser",
        "title": "Translation batching / separators / parser",
        "aliases": [
            "translation batch parser separators order",
            "translation batching split parse",
            "пакетный перевод разделители парсер",
        ],
        "keywords": ["translation", "batch", "parser", "separator", "split", "parse", "перевод", "пакет", "разделител", "парсер"],
        "primary": "videotranslator/translation/batching.py",
        "support": ["videotranslator/pipeline/translation.py"],
        "symbols": ["build_translation_batch", "parse_translation_batch", "split_translation_batches"],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "TranslationBatchTests.test_batch_parser_requires_every_separator_in_order",
            "TranslationBatchTests.test_invalid_batch_falls_back_to_individual_translation",
        ],
        "avoid": ["videotranslator/ui/", "videotranslator/media/"],
        "default_lines": 90,
    },
    {
        "id": "manual_review",
        "title": "Manual translation review UI",
        "aliases": [
            "manual translation review window editor",
            "review before tts editor",
            "ручная правка перевода окно редактор",
        ],
        "keywords": ["manual", "translation", "review", "window", "editor", "tts", "ручн", "правк", "перевод", "окн", "редактор"],
        "primary": "videotranslator/ui/review_flow.py",
        "support": ["videotranslator/ui/review_window.py", "videotranslator/pipeline/process.py"],
        "symbols": [],
        "phases": ["VT4"],
        "phase_routes": {"VT4": ["manual review editor translation"]},
        "tests": [],
        "avoid": ["videotranslator/media/", "videotranslator/tts/cache.py"],
        "default_lines": 110,
    },
    {
        "id": "edge_tts_network",
        "title": "Edge TTS / gTTS / VPN / voice fallback",
        "aliases": [
            "edge tts gtts voice vpn fallback speech synthesis",
            "edge voice fallback vpn",
            "озвучка edge tts gtts vpn голос fallback",
            "пропал выбранный голос edge",
        ],
        "keywords": ["edge", "tts", "gtts", "voice", "vpn", "fallback", "speech", "озвуч", "голос", "сеть"],
        "primary": "videotranslator/pipeline/tts_generate.py",
        "support": ["videotranslator/pipeline/network_voice.py", "videotranslator/network/guard.py"],
        "symbols": ["TTSGenerateMixin.generate_tts", "TTSGenerateMixin._run_edge_tts", "TTSGenerateMixin._edge_tts_async"],
        "phases": ["TG1", "TG2", "TG3", "TG4", "TG5"],
        "phase_routes": {
            "TG1": ["edge cache selected voice"],
            "TG2": ["edge attempt retry network storm vpn selected voice"],
            "TG3": ["fallback gate preserve selected voice allow gtts"],
            "TG4": ["gtts fallback google tts retry"],
            "TG5": ["tts exhausted failure recovery"],
        },
        "tests": [
            "NetworkAndCacheTests.test_tts_recovery_sleeps_only_after_the_current_failed_segment",
            "NetworkAndCacheTests.test_native_rate_failure_does_not_call_rate_ignorant_gtts",
            "NetworkAndCacheTests.test_edge_storm_waits_for_selected_voice_before_gtts_fallback",
            "NetworkAndCacheTests.test_successful_manual_vpn_probe_restores_selected_edge_voice",
        ],
        "avoid": ["videotranslator/ui/review_window.py", "videotranslator/diagnostics/report_render.py"],
        "default_lines": 130,
    },
    {
        "id": "network_guard",
        "title": "Network storm guard / reusable translator session",
        "aliases": [
            "network storm guard session translator timeout",
            "network recovery storm requests",
            "сетевой шторм пауза восстановление",
            "одна сессия google translator",
        ],
        "keywords": ["network", "storm", "guard", "session", "translator", "timeout", "requests", "сеть", "шторм", "сесс"],
        "primary": "videotranslator/network/guard.py",
        "support": ["videotranslator/network/http.py", "videotranslator/pipeline/network_voice.py"],
        "symbols": ["NetworkStormGuard.record_failure", "NetworkStormGuard.record_success", "ReusableGoogleTranslator.__init__"],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "NetworkAndCacheTests.test_network_storm_pauses_once_and_reports_recovery",
            "NetworkAndCacheTests.test_edge_storm_uses_bypass_and_only_ends_after_spaced_probes",
            "NetworkAndCacheTests.test_reusable_translator_uses_one_session",
        ],
        "avoid": ["videotranslator/media/", "videotranslator/ui/review_window.py"],
        "default_lines": 120,
    },
    {
        "id": "tts_prepare",
        "title": "Prepared TTS WAV / speed / local audio preparation",
        "aliases": [
            "tts prepare audio stretch prepared wav speed",
            "prepare tts wav tempo",
            "подготовка wav озвучки скорость",
        ],
        "keywords": ["tts", "prepare", "audio", "stretch", "wav", "speed", "tempo", "подготов", "озвуч", "скорост"],
        "primary": "videotranslator/pipeline/tts_prepare.py",
        "support": ["videotranslator/media/audio.py", "videotranslator/tts/cache.py"],
        "symbols": ["TTSPrepareMixin._get_prepared_tts_audio", "TTSPrepareMixin._prepare_tts_segment"],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "NetworkAndCacheTests.test_prepared_tts_cache_separates_edge_and_gtts",
            "TTSParallelTests.test_speed_limit_is_normalized_to_supported_value",
        ],
        "avoid": ["videotranslator/ui/", "videotranslator/media/final_video.py"],
        "default_lines": 120,
    },
    {
        "id": "pause_sync",
        "title": "Pause Sync / speech slots / TTS timeline planning",
        "aliases": [
            "pause sync timing stop frame timeline speech slots",
            "pause sync plan speech boundary",
            "синхронизация пауз стоп кадры таймлайн",
            "паузы речи слоты",
        ],
        "keywords": ["pause", "sync", "timing", "timeline", "speech", "slot", "stop", "frame", "пауза", "синхрон", "таймлайн", "реч"],
        "primary": "videotranslator/pipeline/timeline_build.py",
        "support": ["videotranslator/sync/pause.py", "videotranslator/pipeline/tts_prepare.py"],
        "symbols": ["TimelineBuildMixin.build_timeline"],
        "phases": ["TL1", "TL4"],
        "phase_routes": {
            "TL1": ["plan jobs timeline slots"],
            "TL4": ["pause plan speech boundary stop frame"],
        },
        "tests": [
            "PausePlanTests.test_nearby_pauses_merge_only_without_speech_boundary",
            "TTSParallelTests.test_parallel_preparation_keeps_timeline_order",
        ],
        "avoid": ["videotranslator/diagnostics/", "videotranslator/ui/review_window.py"],
        "default_lines": 120,
    },
    {
        "id": "timeline_mix",
        "title": "Timeline WAV mixing / amix / numpy / master",
        "aliases": [
            "timeline mix amix numpy wav master voice",
            "mix tts wav master audio",
            "смешивание wav amix numpy мастер озвучки",
        ],
        "keywords": ["timeline", "mix", "amix", "numpy", "wav", "master", "voice", "смеш", "мастер", "озвуч"],
        "primary": "videotranslator/pipeline/timeline_mix.py",
        "support": ["videotranslator/media/audio.py"],
        "symbols": ["TimelineMixMixin._mix_in_batches", "TimelineMixMixin._amix_wavs", "TimelineMixMixin._mix_numpy"],
        "phases": ["TL5"],
        "phase_routes": {"TL5": ["mix master wav amix numpy"]},
        "tests": ["TTSParallelTests.test_parallel_preparation_keeps_timeline_order"],
        "avoid": ["videotranslator/network/", "videotranslator/ui/"],
        "default_lines": 110,
    },
    {
        "id": "ffmpeg_process",
        "title": "FFmpeg/FFprobe subprocess lifecycle / timeout / cancel",
        "aliases": [
            "ffmpeg ffprobe subprocess timeout process kill cancel",
            "ffmpeg timeout process stuck",
            "ffmpeg завис таймаут процесс отмена",
        ],
        "keywords": ["ffmpeg", "ffprobe", "subprocess", "timeout", "process", "kill", "cancel", "завис", "таймаут", "процесс", "отмен"],
        "primary": "videotranslator/media/process.py",
        "support": ["videotranslator/core/cancel.py"],
        "symbols": ["run_subprocess", "find_ffmpeg", "find_ffprobe"],
        "phases": [],
        "phase_routes": {},
        "tests": ["ProblemLoggerTests.test_failed_subprocess_log_has_code_command_and_stderr"],
        "avoid": ["videotranslator/ui/", "videotranslator/pipeline/translation.py"],
        "default_lines": 120,
    },
    {
        "id": "media_probe",
        "title": "Media probe / duration / audio extraction / output validation",
        "aliases": [
            "media duration audio extraction whisper output validation",
            "ffprobe duration extract audio validate output",
            "длительность видео извлечь аудио проверить результат",
        ],
        "keywords": ["media", "duration", "audio", "extract", "validation", "ffprobe", "whisper", "длитель", "извлеч", "провер"],
        "primary": "videotranslator/media/probe.py",
        "support": ["videotranslator/media/process.py"],
        "symbols": ["get_media_duration", "extract_audio_for_whisper", "output_has_video_and_audio"],
        "phases": ["VT1"],
        "phase_routes": {"VT1": ["audio extract whisper input"]},
        "tests": ["NetworkAndCacheTests.test_wav_duration_does_not_start_ffprobe"],
        "avoid": ["videotranslator/pipeline/translation.py", "videotranslator/ui/review_window.py"],
        "default_lines": 100,
    },
    {
        "id": "final_mp4",
        "title": "Final MP4 / NVENC / mux / copy / reencode fallback",
        "aliases": [
            "final mp4 encoder nvenc mux reencode stream copy",
            "final video nvenc timeout",
            "final mp4 nvenc timeout",
            "финальная сборка видео nvenc mp4 mux",
            "итоговое видео кодировщик",
        ],
        "keywords": ["final", "mp4", "video", "encoder", "nvenc", "mux", "reencode", "copy", "финал", "сборк", "видео", "кодиров"],
        "primary": "videotranslator/media/final_video.py",
        "support": ["videotranslator/media/process.py", "videotranslator/media/probe.py"],
        "symbols": ["assemble_final_video"],
        "phases": ["FM1A", "FM1B", "FM2", "FM3", "FM4"],
        "phase_routes": {
            "FM1A": ["filter build pause sync filter"],
            "FM1B": ["encoder attempt nvenc hardware encoder timeout"],
            "FM2": ["audio graph original audio mix"],
            "FM3": ["fast copy stream copy mux"],
            "FM4": ["reencode fallback software encode"],
        },
        "tests": [],
        "avoid": ["videotranslator/ui/review_", "videotranslator/pipeline/translation.py", "videotranslator/tts/"],
        "default_lines": 120,
    },
    {
        "id": "audio_processing",
        "title": "Audio DSP / tempo / loudness / mastering",
        "aliases": [
            "audio tempo loudness limiter rubberband highpass lowpass",
            "master voice audio filters",
            "аудио громкость фильтры highpass lowpass loudness",
        ],
        "keywords": ["audio", "tempo", "loudness", "limiter", "rubberband", "highpass", "lowpass", "master", "аудио", "громк", "фильтр"],
        "primary": "videotranslator/media/audio.py",
        "support": ["videotranslator/pipeline/timeline_mix.py"],
        "symbols": ["master_voice_audio", "build_atempo_filter", "calc_audio_work_timeout"],
        "phases": ["TL5"],
        "phase_routes": {"TL5": ["mix master audio"]},
        "tests": ["TTSParallelTests.test_speed_limit_is_normalized_to_supported_value"],
        "avoid": ["videotranslator/ui/", "videotranslator/pipeline/translation.py"],
        "default_lines": 120,
    },
    {
        "id": "diagnostics_logger",
        "title": "ProblemLogger lifecycle / event recording",
        "aliases": [
            "problem logger events jsonl session",
            "problem logs diagnostic events",
            "логи проблем события jsonl сессия",
        ],
        "keywords": ["problem", "logger", "log", "event", "jsonl", "session", "diagnostic", "лог", "проблем", "событ", "сесс"],
        "primary": "videotranslator/diagnostics/problem_logger.py",
        "support": ["videotranslator/diagnostics/problem_analysis.py", "videotranslator/diagnostics/summary_update.py"],
        "symbols": ["ProblemLogger.event", "ProblemLogger.write_exception", "ProblemLogger.close"],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "ProblemLoggerTests.test_each_session_has_own_complete_codex_artifact_folder",
            "ProblemLoggerTests.test_jsonl_is_split_by_level_and_redacts_tokens",
        ],
        "avoid": ["videotranslator/ui/", "videotranslator/media/final_video.py"],
        "default_lines": 120,
    },
    {
        "id": "diagnostics_analysis",
        "title": "Diagnostic classification / Codex issue cards / grouping",
        "aliases": [
            "problem logs codex diagnostics analysis issue card",
            "diagnostic classify group repeated problems",
            "анализ логов проблем codex карточка причины",
        ],
        "keywords": ["diagnostic", "analysis", "issue", "card", "classify", "group", "problem", "codex", "анализ", "проблем", "причин"],
        "primary": "videotranslator/diagnostics/problem_analysis.py",
        "support": ["videotranslator/core/diagnostics.py", "videotranslator/diagnostics/problem_logger.py"],
        "symbols": [],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "ProblemLoggerTests.test_repeated_problems_are_grouped_and_long_lists_report_truncation",
            "ProblemLoggerTests.test_repeated_problem_sampling_keeps_exact_totals_and_reduces_jsonl",
        ],
        "avoid": ["videotranslator/ui/", "videotranslator/pipeline/timeline_build.py"],
        "default_lines": 120,
    },
    {
        "id": "diagnostics_report",
        "title": "Diagnostic report / manifest / latest index rendering",
        "aliases": [
            "diagnostic report manifest markdown latest index",
            "report md manifest problem logs",
            "отчет диагностики manifest markdown",
        ],
        "keywords": ["diagnostic", "report", "manifest", "markdown", "latest", "index", "отчет", "манифест", "лог"],
        "primary": "videotranslator/diagnostics/report_render.py",
        "support": ["videotranslator/diagnostics/problem_logger.py", "videotranslator/diagnostics/summary_update.py"],
        "symbols": ["ProblemReportRenderMixin._render_report_locked", "ProblemReportRenderMixin._write_manifest_locked"],
        "phases": [],
        "phase_routes": {},
        "tests": ["ProblemLoggerTests.test_each_session_has_own_complete_codex_artifact_folder"],
        "avoid": ["videotranslator/ui/", "videotranslator/pipeline/"],
        "default_lines": 110,
    },
    {
        "id": "diagnostics_summary",
        "title": "Diagnostic summary counters / state JSON",
        "aliases": [
            "diagnostic summary counters state json",
            "problem summary batch counters",
            "сводка диагностики счетчики состояние json",
        ],
        "keywords": ["diagnostic", "summary", "counter", "state", "json", "batch", "сводк", "счетчик", "состоя"],
        "primary": "videotranslator/diagnostics/summary_update.py",
        "support": ["videotranslator/diagnostics/problem_logger.py"],
        "symbols": ["ProblemSummaryMixin._update_summary_locked", "ProblemSummaryMixin._write_summary_locked"],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "ProblemLoggerTests.test_summary_resets_per_file_state_and_tracks_batch",
            "ProblemLoggerTests.test_problem_summary_tracks_voice_fallback_state",
            "ProblemLoggerTests.test_problem_summary_tracks_tts_cache_cleanup",
        ],
        "avoid": ["videotranslator/ui/", "videotranslator/media/"],
        "default_lines": 120,
    },
    {
        "id": "tts_cache",
        "title": "TTS cache / recovery / cleanup / size",
        "aliases": [
            "cache tts recovery size cleanup",
            "tts cache prepared wav retention",
            "кэш tts очистка размер восстановление",
        ],
        "keywords": ["tts", "cache", "recovery", "size", "cleanup", "prepared", "retention", "кэш", "очист", "размер", "восстанов"],
        "primary": "videotranslator/tts/cache.py",
        "support": ["videotranslator/pipeline/tts_prepare.py"],
        "symbols": ["TTSCache.key_lock", "TTSCache.store", "TTSCache.release_used_entries", "TTSCache.cleanup_stale_entries"],
        "phases": [],
        "phase_routes": {},
        "tests": [
            "NetworkAndCacheTests.test_tts_cache_survives_new_instance",
            "NetworkAndCacheTests.test_success_cleanup_removes_only_entries_used_by_current_video",
            "NetworkAndCacheTests.test_prepared_wav_expires_before_compact_recovery_cache",
            "NetworkAndCacheTests.test_size_limit_removes_prepared_wav_before_older_raw_cache",
            "TTSCacheLockPoolTests.test_key_locks_are_bounded_and_same_key_is_stable",
        ],
        "avoid": ["videotranslator/ui/", "videotranslator/media/final_video.py"],
        "default_lines": 120,
    },
    {
        "id": "language_voice_catalog",
        "title": "Language / voice catalog",
        "aliases": [
            "language voice list target language catalog",
            "target language voices",
            "языки голоса список язык перевода",
        ],
        "keywords": ["language", "voice", "list", "target", "catalog", "язык", "голос", "список"],
        "primary": "videotranslator/config_languages.py",
        "support": ["videotranslator/config.py", "videotranslator/ui/settings.py"],
        "symbols": [],
        "phases": [],
        "phase_routes": {},
        "tests": [],
        "avoid": ["videotranslator/pipeline/process.py", "videotranslator/diagnostics/"],
        "default_lines": 90,
    },
    {
        "id": "runtime_config",
        "title": "Runtime limits / timeouts / audio defaults",
        "aliases": [
            "runtime numeric settings limits timeouts audio defaults",
            "timeouts limits config constants",
            "настройки таймауты лимиты аудио по умолчанию",
        ],
        "keywords": ["runtime", "settings", "limits", "timeouts", "audio", "defaults", "config", "настрой", "таймаут", "лимит"],
        "primary": "videotranslator/config.py",
        "support": ["videotranslator/config_languages.py"],
        "symbols": ["normalize_audio_settings"],
        "phases": [],
        "phase_routes": {},
        "tests": ["TTSParallelTests.test_speed_limit_is_normalized_to_supported_value"],
        "avoid": ["videotranslator/ui/review_window.py", "videotranslator/diagnostics/problem_analysis.py"],
        "default_lines": 100,
    },
    {
        "id": "output_reports",
        "title": "Translated text / translation report output",
        "aliases": [
            "translated text segments report output",
            "translation report txt save",
            "сохранение переведенного текста отчет",
        ],
        "keywords": ["translated", "text", "segments", "report", "output", "save", "перевед", "текст", "отчет", "сохран"],
        "primary": "videotranslator/reports/output.py",
        "support": ["videotranslator/pipeline/process.py"],
        "symbols": ["write_translation_report", "write_translated_text_file"],
        "phases": ["VT7"],
        "phase_routes": {"VT7": ["reports success translated text"]},
        "tests": [],
        "avoid": ["videotranslator/pipeline/tts_", "videotranslator/ui/"],
        "default_lines": 100,
    },
    {
        "id": "runtime_paths",
        "title": "Program directory / logs / runtime paths",
        "aliases": [
            "program directory logs paths runtime files",
            "runtime path program dir logs",
            "путь программы логи runtime папки",
        ],
        "keywords": ["program", "directory", "logs", "paths", "runtime", "files", "путь", "программ", "логи", "папк"],
        "primary": "videotranslator/core/paths.py",
        "support": ["videotranslator/bootstrap.py"],
        "symbols": ["get_program_dir", "get_logs_dir", "get_problem_logs_dir", "get_translated_texts_dir"],
        "phases": [],
        "phase_routes": {},
        "tests": [],
        "avoid": ["videotranslator/pipeline/process.py", "videotranslator/ui/review_window.py"],
        "default_lines": 80,
    },
    {
        "id": "compatibility_api",
        "title": "Legacy video_translator API / monkeypatch bridge",
        "aliases": [
            "legacy api import video_translator monkeypatch compatibility",
            "public api compatibility bridge",
            "совместимость старого api monkeypatch import video_translator",
        ],
        "keywords": ["legacy", "api", "import", "video_translator", "monkeypatch", "compatibility", "bridge", "совмест", "стар", "апи"],
        "primary": "videotranslator/public_api.py",
        "support": ["videotranslator/core/compat_bridge.py", "video_translator.py"],
        "symbols": [],
        "phases": [],
        "phase_routes": {},
        "tests": [],
        "avoid": ["videotranslator/pipeline/process.py", "videotranslator/ui/review_window.py"],
        "default_lines": 100,
    },
    {
        "id": "gui_layout",
        "title": "Tkinter layout / controls / settings",
        "aliases": [
            "gui interface tkinter button window settings",
            "tkinter layout controls",
            "интерфейс tkinter кнопки настройки окно",
        ],
        "keywords": ["gui", "interface", "tkinter", "button", "window", "settings", "layout", "интерфейс", "кнопк", "настрой", "окно"],
        "primary": "videotranslator/ui/layout.py",
        "support": ["videotranslator/ui/controls.py", "videotranslator/ui/settings.py", "videotranslator/ui/app.py"],
        "symbols": [],
        "phases": [],
        "phase_routes": {},
        "tests": [],
        "avoid": ["videotranslator/pipeline/process.py", "videotranslator/diagnostics/problem_analysis.py"],
        "default_lines": 100,
    },
]


WORD_RE = re.compile(r"[a-zа-яё0-9_]+", re.IGNORECASE)


def tokens(text: str) -> list[str]:
    return [item.lower() for item in WORD_RE.findall(str(text or "")) if len(item) >= 2]


def _term_score(query_tokens: list[str], phrase: str, *, weight: int) -> int:
    words = tokens(phrase)
    if not words:
        return 0
    qset = set(query_tokens)
    exact = sum(1 for word in words if word in qset)
    prefix = 0
    for q in qset:
        if len(q) < 4:
            continue
        if any(word.startswith(q) or q.startswith(word) for word in words):
            prefix += 1
    return exact * weight + prefix * max(1, weight // 3)


def score_card(query: str, card: dict) -> int:
    query_l = str(query or "").lower().strip()
    q = tokens(query_l)
    if not q:
        return 0
    score = 0
    if query_l == card["id"].lower():
        score += 200
    for alias in card.get("aliases", []):
        alias_l = alias.lower()
        if alias_l in query_l or query_l in alias_l:
            score += 45
        score += _term_score(q, alias, weight=7)
    score += _term_score(q, " ".join(card.get("keywords", [])), weight=10)
    score += _term_score(q, card.get("title", ""), weight=5)
    score += _term_score(q, card.get("primary", ""), weight=4)
    for path in card.get("support", []):
        score += _term_score(q, path, weight=1)
    return score


def rank_cards(query: str) -> list[tuple[int, dict]]:
    ranked = [(score_card(query, card), card) for card in TASK_CARDS]
    ranked = [item for item in ranked if item[0] > 0]
    return sorted(ranked, key=lambda item: (-item[0], item[1]["id"]))


def best_card(query: str) -> dict | None:
    ranked = rank_cards(query)
    return ranked[0][1] if ranked else None


def score_phase(query: str, card: dict, phase_id: str) -> int:
    text = " ".join((card.get("phase_routes") or {}).get(phase_id, []))
    q = tokens(query)
    score = _term_score(q, text, weight=10)
    if phase_id.lower() in q:
        score += 100
    return score


def best_phases(query: str, card: dict, limit: int = 2) -> list[str]:
    phases = list(card.get("phases") or [])
    if not phases:
        return []
    ranked = [(score_phase(query, card, phase_id), phase_id) for phase_id in phases]
    ranked.sort(key=lambda item: (-item[0], phases.index(item[1])))
    positive = [phase_id for score, phase_id in ranked if score > 0]
    if positive:
        return positive[:limit]
    # A card may cover several phases; do not pull all of them by default.
    return phases[:1]


def legacy_task_routes() -> dict[str, list[str]]:
    """Compatibility projection for CODE_MAP consumers from v3."""
    result = {}
    for card in TASK_CARDS:
        route = " ".join(card.get("keywords") or []) or card["title"]
        result[route] = [card["primary"], *card.get("support", [])]
    return result
