"""Reproducible Windows PyInstaller build for Video Translator PRO.

Default is ONEDIR because native/ML dependencies are easier to diagnose and
more reliable there. Pass --onefile only after the ONEDIR artifact works on the
target Windows machine.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "VideoTranslatorPRO"

# These packages are imported lazily/dynamically in the application, so a
# frozen build must explicitly collect them. Local model DATA is intentionally
# not bundled; users install/download it from the Models tab into their profile.
COLLECT_ALL = (
    "whisper",
    "tiktoken",
    "edge_tts",
    "gtts",
    "deep_translator",
    "moviepy",
    "argostranslate",
    "piper",
    "transformers",
    "sentencepiece",
    "huggingface_hub",
)

# Runtime model maintenance compares the installed piper-tts distribution
# version with the compatibility pin. --collect-all does not guarantee that
# importlib.metadata can see distribution metadata in a frozen application.
COPY_METADATA = ("piper-tts",)


def run(argv: list[str]) -> None:
    print("+", subprocess.list2cmdline(argv))
    completed = subprocess.run(argv, cwd=ROOT)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onefile", action="store_true", help="build one-file artifact after ONEDIR has been validated")
    parser.add_argument("--console", action="store_true", help="show console window for diagnostic builds")
    args = parser.parse_args()

    mode = "--onefile" if args.onefile else "--onedir"
    window = "--console" if args.console else "--windowed"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        mode,
        window,
        "--name",
        APP_NAME,
    ]
    for package in COLLECT_ALL:
        cmd.extend(["--collect-all", package])
    for distribution in COPY_METADATA:
        cmd.extend(["--copy-metadata", distribution])

    # A distributable build must carry its own FFmpeg pair. Prefer binaries
    # next to source, otherwise use the pair visible in PATH on the build PC.
    # build_exe.bat runs ensure_ffmpeg_windows.py first on Windows.
    resolved_binaries = []
    for binary_name in ("ffmpeg.exe", "ffprobe.exe"):
        local = ROOT / binary_name
        resolved = local if local.is_file() else None
        if resolved is None:
            configured = os.environ.get(binary_name.removesuffix(".exe").upper() + "_BINARY")
            if configured:
                candidate = Path(configured).resolve()
                resolved = candidate if candidate.is_file() else None
        if resolved is None:
            candidate = shutil.which(binary_name) or shutil.which(binary_name.removesuffix(".exe"))
            resolved = Path(candidate).resolve() if candidate else None
        if resolved is None or not resolved.is_file():
            raise SystemExit(
                f"FFmpeg dependency missing: {binary_name}. "
                "Run tools/ensure_ffmpeg_windows.py --install on Windows first."
            )
        resolved_binaries.append(resolved)
    for binary in resolved_binaries:
        cmd.extend(["--add-binary", f"{binary}{';' if sys.platform == 'win32' else ':'}."])

    cmd.append(str(ROOT / "video_translator.py"))
    run(cmd)

    dist_root = ROOT / "dist"
    output_dir = dist_root if args.onefile else dist_root / APP_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("README_RU.txt", "CHANGELOG_RU.txt", "requirements-optional-ai.txt"):
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, output_dir / name)

    print(f"\nPASS: artifact -> {output_dir}")
    if args.onefile:
        print("NOTE: onefile is NOT a substitute for representative Windows runtime verification.")
    else:
        print("Next: launch dist\\VideoTranslatorPRO\\VideoTranslatorPRO.exe on Windows from Explorer.")


if __name__ == "__main__":
    main()
