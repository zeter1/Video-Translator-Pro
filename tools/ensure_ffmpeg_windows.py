"""Ensure a portable FFmpeg + ffprobe pair is available for Windows.

The source app can use an existing PATH installation.  When --install is used
and neither a local pair nor a PATH pair exists, download Gyan's Windows
release-essentials ZIP, verify its published SHA-256, and place only ffmpeg.exe
and ffprobe.exe next to the application source.  PyInstaller can then bundle
the exact binaries into the distributable artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
GYAN_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
GYAN_SHA256_URL = GYAN_ZIP_URL + ".sha256"
USER_AGENT = "VideoTranslatorPRO/FFmpegBootstrap-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_url_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _download_with_resume(url: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    resume_from = part.stat().st_size if part.is_file() else 0
    headers = {"User-Agent": USER_AGENT}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        status = int(getattr(response, "status", 0) or response.getcode() or 0)
        resumed = bool(resume_from and status == 206)
        if resume_from and not resumed:
            resume_from = 0
        mode = "ab" if resumed else "wb"
        content_length = int(response.headers.get("Content-Length") or 0)
        total = resume_from + content_length if content_length else 0
        done = resume_from
        with part.open(mode) as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
                done += len(chunk)
                if total:
                    print(f"FFmpeg: {done / 1024 / 1024:.0f}/{total / 1024 / 1024:.0f} МБ", end="\r")
    print()
    os.replace(part, target)
    return target


def _path_pair() -> tuple[Path, Path] | None:
    ffmpeg = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe.exe") or shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return Path(ffmpeg).resolve(), Path(ffprobe).resolve()
    return None


def _local_pair() -> tuple[Path, Path] | None:
    ffmpeg = ROOT / "ffmpeg.exe"
    ffprobe = ROOT / "ffprobe.exe"
    if ffmpeg.is_file() and ffprobe.is_file():
        return ffmpeg, ffprobe
    return None


def find_pair() -> tuple[Path, Path] | None:
    return _local_pair() or _path_pair()


def _zip_member(archive: zipfile.ZipFile, executable: str) -> str:
    suffix = "/bin/" + executable.lower()
    matches = [name for name in archive.namelist() if name.lower().replace("\\", "/").endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"В архиве FFmpeg ожидался один {executable}, найдено: {len(matches)}")
    return matches[0]


def install_portable() -> tuple[Path, Path]:
    pair = find_pair()
    if pair:
        return pair
    if sys.platform != "win32":
        raise RuntimeError("Автоматическая установка portable FFmpeg предусмотрена только для Windows.")

    vendor = ROOT / ".vendor"
    archive_path = vendor / "ffmpeg-release-essentials.zip"
    vendor.mkdir(parents=True, exist_ok=True)
    print("FFmpeg/ffprobe не найдены. Скачиваю portable Windows essentials build…")
    _download_with_resume(GYAN_ZIP_URL, archive_path)

    expected_text = _read_url_text(GYAN_SHA256_URL)
    match = re.search(r"\b[0-9a-fA-F]{64}\b", expected_text)
    if not match:
        archive_path.unlink(missing_ok=True)
        raise RuntimeError("Не удалось получить официальный SHA-256 архива FFmpeg.")
    expected = match.group(0).lower()
    actual = _sha256(archive_path)
    if actual != expected:
        archive_path.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 FFmpeg не совпал: expected={expected}, actual={actual}")

    with zipfile.ZipFile(archive_path) as archive:
        for executable in ("ffmpeg.exe", "ffprobe.exe"):
            member = _zip_member(archive, executable)
            temp = ROOT / (executable + ".tmp")
            with archive.open(member) as source, temp.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            os.replace(temp, ROOT / executable)
    archive_path.unlink(missing_ok=True)
    pair = _local_pair()
    if not pair:
        raise RuntimeError("FFmpeg распакован, но обязательная пара ffmpeg.exe/ffprobe.exe не найдена.")
    return pair


def main() -> None:
    parser = argparse.ArgumentParser(description="Check/install FFmpeg + ffprobe for Video Translator PRO on Windows")
    parser.add_argument("--install", action="store_true", help="download verified portable essentials build when no pair is available")
    args = parser.parse_args()
    pair = install_portable() if args.install else find_pair()
    if not pair:
        raise SystemExit("FAIL: ffmpeg/ffprobe not found")
    print(f"PASS ffmpeg: {pair[0]}")
    print(f"PASS ffprobe: {pair[1]}")


if __name__ == "__main__":
    main()
