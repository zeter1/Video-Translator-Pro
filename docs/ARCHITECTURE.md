# ARCHITECTURE — owner-first map

The architecture is optimized for **owner locality**, not maximum file count.

- `video_translator.py` — compatibility launcher.
- `videotranslator/public_api.py` — legacy export/monkeypatch surface.
- `tools/task_catalog.py` — deterministic task → PRIMARY/support/test/budget catalog.
- `docs/CODE_MAP.json` — generated machine graph with canonical ownership.

## Primary feature boundaries

UI: `ui/batch_recovery.py`, `batch_start.py`, `batch_worker.py`, `review_flow.py`, `review_window.py`, layout/controls/settings.

One-video pipeline: `pipeline/process.py` is the state machine; inspect only the needed `VT*` phase. Translation, TTS, Pause Sync and mixing each have separate owners.

Media: `media/process.py` owns subprocess lifecycle; `probe.py` owns probe/extract/validation; `final_video.py` owns MP4/NVENC/fallback.

Diagnostics: logger lifecycle, analysis, report rendering and summary updates are separate owners.

Recovery: `recovery/batch.py` and `recovery/translation.py` own persisted state.

## Facades

`pipeline/translator.py`, `pipeline/timeline.py`, `pipeline/tts.py`, `ui/batch.py`, `ui/review.py`, `media/video.py`, `diagnostics/problem_reporting.py` are compatibility/composition facades. They are **not** primary owners for new substantial behavior.

Use `python tools/find_owner.py "task"` instead of inferring ownership from a filename.
