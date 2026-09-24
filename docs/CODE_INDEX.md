# CODE INDEX — tiny fallback

> Normal Codex path: `python tools/find_owner.py "task"` → `python tools/codex_context.py "task" --code`.
> Do **not** read this file or `CODE_MAP.json` for a normal local change.

## Task cards

| ID | Primary owner | Support | Budget |
|---|---|---|---:|
| `startup_recovery_offer` | `videotranslator/ui/batch_recovery.py` | `videotranslator/recovery/batch.py` | 100 |
| `persistent_task_queue` | `videotranslator/recovery/tasks.py` | `videotranslator/ui/tasks.py`, `videotranslator/ui/batch_start.py` | 180 |
| `batch_recovery_state` | `videotranslator/recovery/batch.py` | `videotranslator/ui/batch_recovery.py` | 120 |
| `batch_start` | `videotranslator/ui/batch_start.py` | `videotranslator/recovery/batch.py`, `videotranslator/ui/queue.py` | 120 |
| `batch_worker` | `videotranslator/ui/batch_worker.py` | `videotranslator/recovery/batch.py`, `videotranslator/pipeline/translator.py` | 120 |
| `video_pipeline` | `videotranslator/pipeline/process.py` | `videotranslator/pipeline/translator.py` | 140 |
| `whisper` | `videotranslator/speech/whisper.py` | `videotranslator/pipeline/process.py` | 90 |
| `local_ai_models` | `videotranslator/models/manager_v8.py` | `videotranslator/ui/models_tab.py`, `videotranslator/models/catalog.py` | 140 |
| `translation_checkpoint` | `videotranslator/recovery/translation.py` | `videotranslator/pipeline/translation.py`, `videotranslator/pipeline/process.py` | 110 |
| `translation_requests` | `videotranslator/pipeline/translation.py` | `videotranslator/network/guard.py`, `videotranslator/recovery/translation.py` | 130 |
| `translation_batch_parser` | `videotranslator/translation/batching.py` | `videotranslator/pipeline/translation.py` | 90 |
| `manual_review` | `videotranslator/ui/review_flow.py` | `videotranslator/ui/review_window.py`, `videotranslator/pipeline/process.py` | 110 |
| `edge_tts_network` | `videotranslator/pipeline/tts_generate.py` | `videotranslator/pipeline/network_voice.py`, `videotranslator/network/guard.py` | 130 |
| `network_guard` | `videotranslator/network/guard.py` | `videotranslator/network/http.py`, `videotranslator/pipeline/network_voice.py` | 120 |
| `tts_prepare` | `videotranslator/pipeline/tts_prepare.py` | `videotranslator/media/audio.py`, `videotranslator/tts/cache.py` | 120 |
| `pause_sync` | `videotranslator/pipeline/timeline_build.py` | `videotranslator/sync/pause.py`, `videotranslator/pipeline/tts_prepare.py` | 120 |
| `timeline_mix` | `videotranslator/pipeline/timeline_mix.py` | `videotranslator/media/audio.py` | 110 |
| `ffmpeg_process` | `videotranslator/media/process.py` | `videotranslator/core/cancel.py` | 120 |
| `media_probe` | `videotranslator/media/probe.py` | `videotranslator/media/process.py` | 100 |
| `final_mp4` | `videotranslator/media/final_video.py` | `videotranslator/media/process.py`, `videotranslator/media/probe.py` | 120 |
| `audio_processing` | `videotranslator/media/audio.py` | `videotranslator/pipeline/timeline_mix.py` | 120 |
| `diagnostics_logger` | `videotranslator/diagnostics/problem_logger.py` | `videotranslator/diagnostics/problem_analysis.py`, `videotranslator/diagnostics/summary_update.py` | 120 |
| `diagnostics_analysis` | `videotranslator/diagnostics/problem_analysis.py` | `videotranslator/core/diagnostics.py`, `videotranslator/diagnostics/problem_logger.py` | 120 |
| `diagnostics_report` | `videotranslator/diagnostics/report_render.py` | `videotranslator/diagnostics/problem_logger.py`, `videotranslator/diagnostics/summary_update.py` | 110 |
| `diagnostics_summary` | `videotranslator/diagnostics/summary_update.py` | `videotranslator/diagnostics/problem_logger.py` | 120 |
| `tts_cache` | `videotranslator/tts/cache.py` | `videotranslator/pipeline/tts_prepare.py` | 120 |
| `language_voice_catalog` | `videotranslator/config_languages.py` | `videotranslator/config.py`, `videotranslator/ui/settings.py` | 90 |
| `runtime_config` | `videotranslator/config.py` | `videotranslator/config_languages.py` | 100 |
| `output_reports` | `videotranslator/reports/output.py` | `videotranslator/pipeline/process.py` | 100 |
| `runtime_paths` | `videotranslator/core/paths.py` | `videotranslator/bootstrap.py` | 80 |
| `compatibility_api` | `videotranslator/public_api.py` | `videotranslator/core/compat_bridge.py`, `video_translator.py` | 100 |
| `gui_layout` | `videotranslator/ui/layout.py` | `videotranslator/ui/controls.py`, `videotranslator/ui/settings.py` | 100 |

## Long workflow phases

| Phase | Exact owner/range |
|---|---|
| `FM1 PAUSE_SYNC_ENCODE` | `videotranslator/media/final_video.py:327–327` |
| `FM1A FILTER_BUILD` | `videotranslator/media/final_video.py:328–362` |
| `FM1B ENCODER_ATTEMPTS` | `videotranslator/media/final_video.py:363–464` |
| `FM2 AUDIO_GRAPH` | `videotranslator/media/final_video.py:465–483` |
| `FM3 FAST_COPY` | `videotranslator/media/final_video.py:484–563` |
| `FM4 REENCODE_FALLBACK` | `videotranslator/media/final_video.py:564–672` |
| `VT1 AUDIO_EXTRACT` | `videotranslator/pipeline/process.py:227–262` |
| `VT2 WHISPER` | `videotranslator/pipeline/process.py:263–393` |
| `VT3 TRANSLATION` | `videotranslator/pipeline/process.py:394–407` |
| `VT3A CHECKPOINT_PLAN` | `videotranslator/pipeline/process.py:408–497` |
| `VT3B BATCH_TRANSLATE` | `videotranslator/pipeline/process.py:498–605` |
| `VT3C DEFERRED_RETRY` | `videotranslator/pipeline/process.py:606–708` |
| `VT3D TRANSLATION_INTEGRITY` | `videotranslator/pipeline/process.py:709–774` |
| `VT4 MANUAL_REVIEW` | `videotranslator/pipeline/process.py:775–861` |
| `VT5 TTS_TIMELINE` | `videotranslator/pipeline/process.py:862–880` |
| `VT6 FINAL_MUX` | `videotranslator/pipeline/process.py:881–906` |
| `VT7 REPORTS` | `videotranslator/pipeline/process.py:907–1026` |
| `VT8 CLEANUP` | `videotranslator/pipeline/process.py:1027–1060` |
| `TL1 PLAN_JOBS` | `videotranslator/pipeline/timeline_build.py:67–91` |
| `TL2 PARALLEL_TTS` | `videotranslator/pipeline/timeline_build.py:92–130` |
| `TL3 RECOVERY` | `videotranslator/pipeline/timeline_build.py:131–131` |
| `TL3A EDGE_RECOVERY` | `videotranslator/pipeline/timeline_build.py:132–221` |
| `TL3B CONTROLLED_GTTS_FALLBACK` | `videotranslator/pipeline/timeline_build.py:222–305` |
| `TL3C VOICE_CONSISTENCY_REPAIR` | `videotranslator/pipeline/timeline_build.py:306–400` |
| `TL3D READY_TIMELINE` | `videotranslator/pipeline/timeline_build.py:401–445` |
| `TL3E TTS_SUMMARY` | `videotranslator/pipeline/timeline_build.py:446–495` |
| `TL4 PAUSE_PLAN` | `videotranslator/pipeline/timeline_build.py:496–538` |
| `TL5 MIX_MASTER` | `videotranslator/pipeline/timeline_build.py:539–551` |
| `TG1 EDGE_CACHE` | `videotranslator/pipeline/tts_generate.py:109–115` |
| `TG2 EDGE_ATTEMPTS` | `videotranslator/pipeline/tts_generate.py:116–199` |
| `TG3 FALLBACK_GATE` | `videotranslator/pipeline/tts_generate.py:200–302` |
| `TG4 GTTS_FALLBACK` | `videotranslator/pipeline/tts_generate.py:303–395` |
| `TG5 EXHAUSTED` | `videotranslator/pipeline/tts_generate.py:396–419` |
| `BW1 BATCH_INIT` | `videotranslator/ui/batch_worker.py:39–129` |
| `BW2 FILE_LOOP` | `videotranslator/ui/batch_worker.py:130–279` |
| `BW3 RUN_VIDEO` | `videotranslator/ui/batch_worker.py:280–372` |
| `BW4 FINALIZE_BATCH` | `videotranslator/ui/batch_worker.py:373–479` |

Detailed symbols, callers, state reads/writes and test metadata live only in the machine index.
