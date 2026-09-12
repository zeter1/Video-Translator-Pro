# CODE INDEX — tiny fallback

> Normal Codex path: `python tools/find_owner.py "task"` → `python tools/codex_context.py "task" --code`.
> Do **not** read this file or `CODE_MAP.json` for a normal local change.

## Task cards

| ID | Primary owner | Support | Budget |
|---|---|---|---:|
| `startup_recovery_offer` | `videotranslator/ui/batch_recovery.py` | `videotranslator/recovery/batch.py` | 100 |
| `batch_recovery_state` | `videotranslator/recovery/batch.py` | `videotranslator/ui/batch_recovery.py` | 120 |
| `batch_start` | `videotranslator/ui/batch_start.py` | `videotranslator/recovery/batch.py`, `videotranslator/ui/queue.py` | 120 |
| `batch_worker` | `videotranslator/ui/batch_worker.py` | `videotranslator/recovery/batch.py`, `videotranslator/pipeline/translator.py` | 120 |
| `video_pipeline` | `videotranslator/pipeline/process.py` | `videotranslator/pipeline/translator.py` | 140 |
| `whisper` | `videotranslator/speech/whisper.py` | `videotranslator/pipeline/process.py` | 90 |
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
| `FM1 PAUSE_SYNC_ENCODE` | `videotranslator/media/final_video.py:253–253` |
| `FM1A FILTER_BUILD` | `videotranslator/media/final_video.py:254–285` |
| `FM1B ENCODER_ATTEMPTS` | `videotranslator/media/final_video.py:286–387` |
| `FM2 AUDIO_GRAPH` | `videotranslator/media/final_video.py:388–405` |
| `FM3 FAST_COPY` | `videotranslator/media/final_video.py:406–485` |
| `FM4 REENCODE_FALLBACK` | `videotranslator/media/final_video.py:486–594` |
| `VT1 AUDIO_EXTRACT` | `videotranslator/pipeline/process.py:200–235` |
| `VT2 WHISPER` | `videotranslator/pipeline/process.py:236–298` |
| `VT3 TRANSLATION` | `videotranslator/pipeline/process.py:299–312` |
| `VT3A CHECKPOINT_PLAN` | `videotranslator/pipeline/process.py:313–375` |
| `VT3B BATCH_TRANSLATE` | `videotranslator/pipeline/process.py:376–439` |
| `VT3C DEFERRED_RETRY` | `videotranslator/pipeline/process.py:440–470` |
| `VT3D TRANSLATION_INTEGRITY` | `videotranslator/pipeline/process.py:471–510` |
| `VT4 MANUAL_REVIEW` | `videotranslator/pipeline/process.py:511–556` |
| `VT5 TTS_TIMELINE` | `videotranslator/pipeline/process.py:557–575` |
| `VT6 FINAL_MUX` | `videotranslator/pipeline/process.py:576–601` |
| `VT7 REPORTS` | `videotranslator/pipeline/process.py:602–706` |
| `VT8 CLEANUP` | `videotranslator/pipeline/process.py:707–740` |
| `TL1 PLAN_JOBS` | `videotranslator/pipeline/timeline_build.py:67–89` |
| `TL2 PARALLEL_TTS` | `videotranslator/pipeline/timeline_build.py:90–128` |
| `TL3 RECOVERY` | `videotranslator/pipeline/timeline_build.py:129–129` |
| `TL3A EDGE_RECOVERY` | `videotranslator/pipeline/timeline_build.py:130–220` |
| `TL3B CONTROLLED_GTTS_FALLBACK` | `videotranslator/pipeline/timeline_build.py:221–304` |
| `TL3C READY_TIMELINE` | `videotranslator/pipeline/timeline_build.py:305–349` |
| `TL3D TTS_SUMMARY` | `videotranslator/pipeline/timeline_build.py:350–399` |
| `TL4 PAUSE_PLAN` | `videotranslator/pipeline/timeline_build.py:400–415` |
| `TL5 MIX_MASTER` | `videotranslator/pipeline/timeline_build.py:416–428` |
| `TG1 EDGE_CACHE` | `videotranslator/pipeline/tts_generate.py:43–49` |
| `TG2 EDGE_ATTEMPTS` | `videotranslator/pipeline/tts_generate.py:50–133` |
| `TG3 FALLBACK_GATE` | `videotranslator/pipeline/tts_generate.py:134–144` |
| `TG4 GTTS_FALLBACK` | `videotranslator/pipeline/tts_generate.py:145–226` |
| `TG5 EXHAUSTED` | `videotranslator/pipeline/tts_generate.py:227–250` |
| `BW1 BATCH_INIT` | `videotranslator/ui/batch_worker.py:38–75` |
| `BW2 FILE_LOOP` | `videotranslator/ui/batch_worker.py:76–186` |
| `BW3 RUN_VIDEO` | `videotranslator/ui/batch_worker.py:187–275` |
| `BW4 FINALIZE_BATCH` | `videotranslator/ui/batch_worker.py:276–354` |

Detailed symbols, callers, state reads/writes and test metadata live only in the machine index.
