"""Видео Переводчик PRO — compatibility launcher.

Рабочая реализация разнесена по пакету ``videotranslator``.
Старый импорт ``import video_translator`` сохранён.
"""
import importlib
import json
import subprocess
import sys

from videotranslator.core.paths import configure_runtime_path, get_runtime_bin_dir

configure_runtime_path()

from videotranslator.public_api import *  # noqa: F401,F403,E402
from videotranslator.bootstrap import main  # noqa: E402


def _run_packaged_self_test() -> int:
    """Check bundled Python modules and media tools without loading a Whisper model."""
    module_names = (
        "numpy",
        "torch",
        "whisper",
        "edge_tts",
        "gtts",
        "deep_translator",
        "moviepy",
    )
    modules = {}
    for name in module_names:
        try:
            importlib.import_module(name)
            modules[name] = True
        except Exception as exc:
            modules[name] = f"{type(exc).__name__}: {exc}"

    runtime_dir = get_runtime_bin_dir()
    tools = {}
    for name, args in {
        "ffmpeg.exe": ["-version"],
        "ffprobe.exe": ["-version"],
    }.items():
        path = runtime_dir / name
        item = {"path": str(path), "exists": path.exists(), "returncode": None}
        if path.exists():
            try:
                completed = subprocess.run(
                    [str(path), *args],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                )
                item["returncode"] = completed.returncode
                item["output"] = (completed.stdout or "")[:500]
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
        tools[name] = item

    payload = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "runtime_bin_dir": str(runtime_dir),
        "modules": modules,
        "tools": tools,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    modules_ok = all(value is True for value in modules.values())
    tools_ok = all(item.get("exists") and item.get("returncode") == 0 for item in tools.values())
    return 0 if modules_ok and tools_ok else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_run_packaged_self_test())
    main()
