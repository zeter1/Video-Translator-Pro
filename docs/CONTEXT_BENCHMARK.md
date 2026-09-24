# CONTEXT BENCHMARK

Representative Codex tasks routed through `tools/codex_context.py --code`.
Python package size used as comparison baseline: **16680 source lines**.

| Task | Card | Primary | Source lines | Budget | Reduction vs package |
|---|---|---|---:|---:|---:|
| batch recovery empty path | `batch_recovery_state` | `videotranslator/recovery/batch.py` | 117 | 120 | 99.3% |
| restore previous batch on startup | `startup_recovery_offer` | `videotranslator/ui/batch_recovery.py` | 100 | 100 | 99.4% |
| translation checkpoint resume incomplete text | `translation_checkpoint` | `videotranslator/recovery/translation.py` | 61 | 110 | 99.6% |
| google translator retry failed segment | `translation_requests` | `videotranslator/pipeline/translation.py` | 130 | 130 | 99.2% |
| edge tts vpn fallback selected voice | `edge_tts_network` | `videotranslator/pipeline/tts_generate.py` | 93 | 130 | 99.4% |
| pause sync speech boundary timeline | `pause_sync` | `videotranslator/pipeline/timeline_build.py` | 43 | 120 | 99.7% |
| final mp4 nvenc timeout | `final_mp4` | `videotranslator/media/final_video.py` | 102 | 120 | 99.4% |
| ffmpeg subprocess timeout cancel | `ffmpeg_process` | `videotranslator/media/process.py` | 116 | 120 | 99.3% |
| tts cache cleanup size recovery | `tts_cache` | `videotranslator/tts/cache.py` | 120 | 120 | 99.3% |
| diagnostic report manifest markdown | `diagnostics_report` | `videotranslator/diagnostics/report_render.py` | 110 | 110 | 99.3% |
| batch worker multiple files progress | `batch_worker` | `videotranslator/ui/batch_worker.py` | 91 | 120 | 99.5% |
| whisper gpu cuda model | `whisper` | `videotranslator/speech/whisper.py` | 15 | 90 | 99.9% |

**Result: PASS — deterministic routing and hard source budgets held for every benchmark case.**
