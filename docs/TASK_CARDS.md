# TASK CARDS — deterministic Codex router

This is a human fallback. The tools consume the same cards directly from `tools/task_catalog.py`.

| ID | Intent | Primary | Support | Tests |
|---|---|---|---|---:|
| `startup_recovery_offer` | Startup batch recovery offer | `videotranslator/ui/batch_recovery.py` | `videotranslator/recovery/batch.py` | 2 |
| `persistent_task_queue` | Persistent translation task queue / Tasks tab / restart resume | `videotranslator/recovery/tasks.py` | `videotranslator/ui/tasks.py`, `videotranslator/ui/batch_start.py` | 31 |
| `batch_recovery_state` | Batch recovery persisted state / problem-log reconstruction | `videotranslator/recovery/batch.py` | `videotranslator/ui/batch_recovery.py` | 3 |
| `batch_start` | Batch start / validation / output directory | `videotranslator/ui/batch_start.py` | `videotranslator/recovery/batch.py`, `videotranslator/ui/queue.py` | 2 |
| `batch_worker` | Batch worker / multiple files / progress | `videotranslator/ui/batch_worker.py` | `videotranslator/recovery/batch.py`, `videotranslator/pipeline/translator.py` | 2 |
| `video_pipeline` | One-video pipeline orchestration | `videotranslator/pipeline/process.py` | `videotranslator/pipeline/translator.py` | 0 |
| `whisper` | Whisper transcription / GPU | `videotranslator/speech/whisper.py` | `videotranslator/pipeline/process.py` | 0 |
| `local_ai_models` | Local AI models / startup update / persistent voices | `videotranslator/models/manager_v8.py` | `videotranslator/ui/models_tab.py`, `videotranslator/models/catalog.py` | 8 |
| `translation_checkpoint` | Translation checkpoint / resume / integrity | `videotranslator/recovery/translation.py` | `videotranslator/pipeline/translation.py`, `videotranslator/pipeline/process.py` | 2 |
| `translation_requests` | Google translation / retries / per-segment translation | `videotranslator/pipeline/translation.py` | `videotranslator/network/guard.py`, `videotranslator/recovery/translation.py` | 2 |
| `translation_batch_parser` | Translation batching / separators / parser | `videotranslator/translation/batching.py` | `videotranslator/pipeline/translation.py` | 2 |
| `manual_review` | Manual translation review UI | `videotranslator/ui/review_flow.py` | `videotranslator/ui/review_window.py`, `videotranslator/pipeline/process.py` | 0 |
| `edge_tts_network` | Edge TTS / gTTS / VPN / voice fallback | `videotranslator/pipeline/tts_generate.py` | `videotranslator/pipeline/network_voice.py`, `videotranslator/network/guard.py` | 4 |
| `network_guard` | Network storm guard / reusable translator session | `videotranslator/network/guard.py` | `videotranslator/network/http.py`, `videotranslator/pipeline/network_voice.py` | 3 |
| `tts_prepare` | Prepared TTS WAV / speed / local audio preparation | `videotranslator/pipeline/tts_prepare.py` | `videotranslator/media/audio.py`, `videotranslator/tts/cache.py` | 2 |
| `pause_sync` | Pause Sync / speech slots / TTS timeline planning | `videotranslator/pipeline/timeline_build.py` | `videotranslator/sync/pause.py`, `videotranslator/pipeline/tts_prepare.py` | 2 |
| `timeline_mix` | Timeline WAV mixing / amix / numpy / master | `videotranslator/pipeline/timeline_mix.py` | `videotranslator/media/audio.py` | 1 |
| `ffmpeg_process` | FFmpeg/FFprobe subprocess lifecycle / timeout / cancel | `videotranslator/media/process.py` | `videotranslator/core/cancel.py` | 1 |
| `media_probe` | Media probe / duration / audio extraction / output validation | `videotranslator/media/probe.py` | `videotranslator/media/process.py` | 1 |
| `final_mp4` | Final MP4 / NVENC / mux / copy / reencode fallback | `videotranslator/media/final_video.py` | `videotranslator/media/process.py`, `videotranslator/media/probe.py` | 0 |
| `audio_processing` | Audio DSP / tempo / loudness / mastering | `videotranslator/media/audio.py` | `videotranslator/pipeline/timeline_mix.py` | 1 |
| `diagnostics_logger` | ProblemLogger lifecycle / event recording | `videotranslator/diagnostics/problem_logger.py` | `videotranslator/diagnostics/problem_analysis.py`, `videotranslator/diagnostics/summary_update.py` | 2 |
| `diagnostics_analysis` | Diagnostic classification / Codex issue cards / grouping | `videotranslator/diagnostics/problem_analysis.py` | `videotranslator/core/diagnostics.py`, `videotranslator/diagnostics/problem_logger.py` | 2 |
| `diagnostics_report` | Diagnostic report / manifest / latest index rendering | `videotranslator/diagnostics/report_render.py` | `videotranslator/diagnostics/problem_logger.py`, `videotranslator/diagnostics/summary_update.py` | 1 |
| `diagnostics_summary` | Diagnostic summary counters / state JSON | `videotranslator/diagnostics/summary_update.py` | `videotranslator/diagnostics/problem_logger.py` | 3 |
| `tts_cache` | TTS cache / recovery / cleanup / size | `videotranslator/tts/cache.py` | `videotranslator/pipeline/tts_prepare.py` | 5 |
| `language_voice_catalog` | Language / voice catalog | `videotranslator/config_languages.py` | `videotranslator/config.py`, `videotranslator/ui/settings.py` | 0 |
| `runtime_config` | Runtime limits / timeouts / audio defaults | `videotranslator/config.py` | `videotranslator/config_languages.py` | 1 |
| `output_reports` | Translated text / translation report output | `videotranslator/reports/output.py` | `videotranslator/pipeline/process.py` | 0 |
| `runtime_paths` | Program directory / logs / runtime paths | `videotranslator/core/paths.py` | `videotranslator/bootstrap.py` | 0 |
| `compatibility_api` | Legacy video_translator API / monkeypatch bridge | `videotranslator/public_api.py` | `videotranslator/core/compat_bridge.py`, `video_translator.py` | 0 |
| `gui_layout` | Tkinter layout / controls / settings | `videotranslator/ui/layout.py` | `videotranslator/ui/controls.py`, `videotranslator/ui/settings.py` | 0 |

Each card also defines aliases, `DO NOT READ YET`, preferred symbols/phases, exact test selectors and a source-line budget.
