# AI NAVIGATION — Context v4

Default flow:

```bash
python tools/find_owner.py "task"
python tools/codex_context.py "task"
python tools/codex_context.py "task" --code
python tools/verify_task.py "task"
```

`find_owner.py` chooses one deterministic task-card and one **PRIMARY** owner. `SUPPORT` is not automatic reading; open it only when the primary code proves the change crosses that boundary.

`codex_context.py` uses the task-card source budget (normally 80–140 lines), preferred symbols and `CODEX-PHASE`. It prints `DO NOT READ YET` so broad context does not grow by habit.

Useful diagnostics:

```bash
python tools/change_surface.py "translation checkpoint resume"
python tools/codex_context.py --phase FM1B --code --surface --max-lines 100
```

`change_surface.py` shows compact `self` reads/writes, config constants, calls/callers, path literals and mapped tests without opening neighboring files.

Verification:

```bash
python tools/verify_task.py "batch recovery empty path"
python tools/verify_project.py
```

The first command runs the smallest mapped test set. The second is the full release gate.

`docs/CODE_MAP.json` is machine-only. `CODE_INDEX.md` and `TASK_CARDS.md` are fallback maps, not first-read context.
