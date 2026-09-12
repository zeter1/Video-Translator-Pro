# REFACTOR AUDIT — Codex Context v4 — 2026-09-08

## Objective

Reduce Codex context cost **without further fragmenting working runtime code**. The v4 goal is deterministic task routing: one PRIMARY owner, bounded source, explicit support boundaries and the smallest truthful verification set.

## What changed

- Added **30 deterministic task-cards** in `tools/task_catalog.py`.
- Every card defines:
  - aliases/keywords;
  - exactly one PRIMARY owner;
  - SUPPORT files;
  - preferred symbols/phases;
  - `DO NOT READ YET`;
  - exact regression selectors;
  - a default source-line budget.
- `find_owner.py` now routes by task-card first instead of generic text ranking.
- `codex_context.py` now prints PRIMARY source only by default; support code requires explicit `--support-code`.
- Added `change_surface.py`: compact `self` reads/writes, constants, calls/callers and path literals.
- Added `verify_task.py`: static/import plus only mapped regression selectors.
- Added `context_benchmark.py`: representative task routing/budget benchmark.
- `CODE_MAP.json` schema upgraded to **v3** with canonical ownership and compact change-surface metadata.
- Duplicate compatibility wrappers are marked as forwarders; each duplicate qualified symbol has exactly **one canonical PRIMARY owner**.
- Added `TG1..TG5` phases inside the large TTS generation method so Edge/gTTS bugs no longer require reading the whole method.

## Automatic-context footprint

| Artifact | v3 | v4 |
|---|---:|---:|
| `AGENTS.md` | 4412 bytes | 2765 bytes |
| `CODE_INDEX.md` | 13251 bytes | 7046 bytes |
| deterministic task cards | 0 | 30 |
| CODEX phases | 31 | 36 |

`CODE_MAP.json` became larger because it is a **machine-only index** and now stores ownership/state metadata. It is explicitly excluded from normal model reading.

## Context benchmark

Package source baseline: **9426 Python lines**.

Representative `codex_context.py --code` cases use only **15–127 source lines**. The generated `docs/CONTEXT_BENCHMARK.md` shows **98.7–99.8% fewer source lines than reading the package** for the benchmark tasks.

Examples:
- batch recovery empty path → `recovery/batch.py`, 106 lines;
- final MP4 NVENC timeout → `media/final_video.py` / `FM1B`, 92 lines;
- Edge TTS + VPN fallback → `pipeline/tts_generate.py` / `TG2`, 93 lines;
- Whisper GPU model → `speech/whisper.py`, 15 lines.

## Behavior-preserving evidence

Application source was AST-compared with the uploaded v3 snapshot.

**Result: 0 application AST changes.**

The only application-source edits in v4 are `CODEX-PHASE` comments inside `tts_generate.py`; comments do not alter Python AST or runtime behavior. All other v4 changes are navigation/docs/verification harness changes.

The previous startup recovery fix (`WindowsPath('.') has an empty name`) remains intact.

## Verification gate

`python tools/verify_project.py` passes:

- compile;
- legacy `import video_translator` public API;
- program-directory contract;
- empty/directory batch-recovery guard;
- CODE_MAP v3 generation;
- one canonical owner for every duplicate symbol group;
- deterministic task-card routing;
- hard source budgets;
- targeted verification router;
- context benchmark;
- **43/43 historical regression tests**.

Live Whisper/GPU, Edge TTS/gTTS/VPN, NVENC and long FFmpeg remain runtime-only claims and require real Windows evidence.
