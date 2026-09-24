"""Видео Переводчик PRO — compatibility launcher."""

import importlib
import json
import subprocess
import sys

from videotranslator.core.paths import get_bundled_resource_dir
from videotranslator.public_api import *  # noqa: F401,F403
from videotranslator.bootstrap import main


def _run_packaged_self_test() -> int:
    """Check bundled imports and media tools without starting the GUI or a model."""
    module_names = (
        "numpy", "torch", "whisper", "edge_tts", "gtts", "deep_translator",
        "moviepy", "argostranslate", "transformers", "sentencepiece",
        "huggingface_hub", "piper",
    )
    modules = {}
    for name in module_names:
        try:
            importlib.import_module(name)
            modules[name] = True
        except Exception as exc:
            modules[name] = f"{type(exc).__name__}: {exc}"

    runtime_dir = get_bundled_resource_dir()
    media_tools = {}
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        path = runtime_dir / name
        item = {"exists": path.is_file(), "returncode": None}
        if path.is_file():
            try:
                completed = subprocess.run(
                    [str(path), "-version"], stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, timeout=20,
                )
                item["returncode"] = completed.returncode
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
        media_tools[name] = item

    print(json.dumps({"frozen": bool(getattr(sys, "frozen", False)),
                      "modules": modules, "tools": media_tools},
                     ensure_ascii=False, sort_keys=True))
    return 0 if (all(value is True for value in modules.values())
                 and all(item["exists"] and item["returncode"] == 0
                         for item in media_tools.values())) else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_run_packaged_self_test())
    main()
