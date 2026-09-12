# TEST MAP

The historical suite is `tests/test_video_translator_diagnostics.py` (43 tests).

Do not read the whole test file for a local change. Task-cards contain exact selectors and `tools/verify_task.py "task"` runs only those selectors.

Main coverage families:

| Area | Primary owner |
|---|---|
| problem logs | `diagnostics/problem_logger.py` |
| diagnostic analysis/report/summary | `diagnostics/problem_analysis.py`, `report_render.py`, `summary_update.py` |
| translation checkpoint/retry | `recovery/translation.py`, `pipeline/translation.py` |
| batch recovery | `recovery/batch.py`, `ui/batch_recovery.py`, `ui/batch_worker.py` |
| network/Edge/gTTS/cache | `network/guard.py`, `pipeline/tts_generate.py`, `tts/cache.py` |
| Pause Sync/TTS timeline | `pipeline/timeline_build.py`, `timeline_mix.py` |
| subprocess diagnostics | `media/process.py` |

There is no dedicated unit coverage for every live media path. A task-card with zero selectors means exactly that; use the full suite plus runtime verification when the claim requires it.
