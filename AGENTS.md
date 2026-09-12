# AGENTS.md — Video Translator PRO / Codex Context v4

Goal: **route first, then read ~50–150 source lines**. Do not inspect the repository recursively for a local task.

Project overview: `README_RU.txt` and `docs/ARCHITECTURE.md`; runtime state: `docs/RUNTIME_DATA.md`. Before executing tests/import/startup, follow `docs/VERIFICATION.md` for state isolation and Windows UTF-8.

## Required fast path

```bash
python tools/find_owner.py "symptom or task"
python tools/codex_context.py "same task"
python tools/codex_context.py "same task" --code
```

After editing:

```bash
python tools/verify_task.py "same task"
```

Before handoff or after shared/high-risk changes:

```bash
python tools/verify_project.py
```

## Context rules

1. `find_owner.py` chooses one deterministic **PRIMARY** owner.
2. `codex_context.py --code` enforces the task-card source budget.
3. Open `SUPPORT` only when primary evidence crosses that boundary.
4. Do not open `DO NOT READ YET` for background context.
5. For side effects/state/callers:
   ```bash
   python tools/change_surface.py "same task"
   ```
6. Never start with recursive file listing, all `*.py`, all docs, `CODE_MAP.json`, or all of `pipeline/process.py`.
7. `CODE_MAP.json` is machine-only. `CODE_INDEX.md`/`TASK_CARDS.md` are fallback maps.

## Long workflows

Use exact phase slices:
- `VT1..VT8` one-video pipeline; translation subphases `VT3A..VT3D`
- `TG1..TG5` Edge/gTTS generation
- `TL1..TL5` Pause Sync/TTS timeline
- `BW1..BW4` batch worker
- `FM1A..FM4` final MP4

Example:

```bash
python tools/codex_context.py "final mp4 nvenc timeout" --code
python tools/codex_context.py --phase FM1B --code --max-lines 100
```

## Ownership

Compatibility wrappers are not owners. `CODE_MAP v3` has one canonical `primary_owner` per duplicate symbol. Substantial new logic belongs in the PRIMARY returned by `find_owner.py`, not in facade files.

Facades: `pipeline/translator.py`, `pipeline/timeline.py`, `pipeline/tts.py`, `ui/batch.py`, `ui/review.py`, `media/video.py`, `diagnostics/problem_reporting.py`.

## Compatibility / data safety

Keep historical `import video_translator as vt` and monkeypatch compatibility via `public_api.py` + `core/compat_bridge.py`.

Runtime is not source: `logs/`, `Логи проблем/`, `translated_texts/`, `tts_cache/`, `work/`, `__pycache__/`.

Do not delete source videos. Final success requires verified MP4 video+audio. Recovery/checkpoint is a data-safety contract.

## Verification truthfulness

`verify_task.py` runs static/import plus exact mapped regression selectors. If a task-card has no exact test, do not claim targeted coverage; use the full suite when behavior changed.

`verify_project.py` checks compile, legacy API/program-dir, CODE_MAP v3 canonical ownership, deterministic routing/budgets, targeted verification, context benchmark and the complete regression suite. Opt-in synthetic media checks are described in `docs/VERIFICATION.md`.

Live Whisper/GPU, Edge/gTTS/VPN, NVENC and long FFmpeg require real Windows runtime evidence.
