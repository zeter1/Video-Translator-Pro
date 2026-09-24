# TEST MAP

The historical diagnostics suite is `tests/test_video_translator_diagnostics.py`, but current regression coverage is split across multiple `tests/test_*.py` modules.

Do not read the whole suite for a local change. Task-cards contain exact `Class.test_method` selectors; `tools/verify_task.py "task"` builds an AST class→file index, imports only the owning test module(s), and runs just those selectors.

Main coverage families:

| Area | Primary owner |
|---|---|
| problem logs | `diagnostics/problem_logger.py` |
| diagnostic analysis/report/summary | `diagnostics/problem_analysis.py`, `report_render.py`, `summary_update.py` |
| translation checkpoint/retry | `recovery/translation.py`, `pipeline/translation.py` |
| batch recovery | `recovery/batch.py`, `ui/batch_recovery.py`, `ui/batch_worker.py` |
| network/Edge/gTTS/cache | `network/guard.py`, `pipeline/tts_generate.py`, `tts/cache.py` |
| local AI model store / startup maintenance | `models/manager_v8.py`, `ui/models_tab.py` |
| Pause Sync/TTS timeline | `pipeline/timeline_build.py`, `timeline_mix.py` |
| subprocess diagnostics | `media/process.py` |

There is no dedicated unit coverage for every live media path. A task-card with zero selectors means exactly that; use the full suite plus runtime verification when the claim requires it.
