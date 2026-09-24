"""Owner module for: find_ffmpeg, find_ffprobe, format_subprocess_command, log_subprocess_result, run_subprocess."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import time
from videotranslator.core.paths import get_bundled_resource_dir, get_program_dir
from videotranslator.core.cancel import CancelledError
from videotranslator.core.diagnostics import redact_diagnostic_text
from videotranslator.core.process_flags import no_console_creationflags
from videotranslator.core.timefmt import fmt_time


def find_ffmpeg() -> str:
    # Prefer the exact binary bundled with the artifact, then an external
    # portable pair next to the launcher.  PyInstaller 6 ONEDIR may place
    # bundled data under _internal and ONEFILE extracts it under _MEIPASS, so
    # bundled resources are resolved from __file__, not sys.executable.
    resource_dirs = [get_bundled_resource_dir(), get_program_dir()]
    seen = set()
    for directory in resource_dirs:
        key = os.path.normcase(os.path.abspath(str(directory)))
        if key in seen:
            continue
        seen.add(key)
        for name in ("ffmpeg.exe", "ffmpeg"):
            path = Path(directory) / name
            if path.is_file():
                return str(path)

    for candidate in ("ffmpeg", "ffmpeg.exe"):
        path = shutil.which(candidate)
        if path:
            return path

    raise RuntimeError(
        "ffmpeg не найден!\n"
        "Положите ffmpeg.exe рядом с программой или добавьте ffmpeg в PATH."
    )


def find_ffprobe(ffmpeg_path: str) -> str:
    """Ищет ffprobe рядом с ffmpeg или в PATH."""
    ffmpeg_dir = os.path.dirname(ffmpeg_path)
    for name in ("ffprobe.exe", "ffprobe"):
        local = os.path.join(ffmpeg_dir, name) if ffmpeg_dir else name
        if os.path.exists(local):
            return local

    path = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if path:
        return path

    raise RuntimeError(
        "ffprobe не найден!\n"
        "Положите ffprobe.exe рядом с ffmpeg.exe или добавьте ffprobe в PATH."
    )


def format_subprocess_command(cmd: list, max_len: int = 4000) -> str:
    try:
        rendered = subprocess.list2cmdline([str(part) for part in (cmd or [])])
    except Exception:
        rendered = " ".join(str(part) for part in (cmd or []))
    return redact_diagnostic_text(rendered, max_len=max_len)


def log_subprocess_result(result: subprocess.CompletedProcess, cmd: list, log=None):
    if not log or result.returncode == 0:
        return
    log(f"      ⚠️ Внешний процесс завершился с кодом {result.returncode}")
    log(f"      ⚠️ Команда внешнего процесса: {format_subprocess_command(cmd)}")
    stderr = redact_diagnostic_text((result.stderr or "")[-4000:], max_len=4000).strip()
    stdout = redact_diagnostic_text((result.stdout or "")[-2000:], max_len=2000).strip()
    if stderr:
        log(f"      stderr (хвост): {stderr}")
    elif stdout:
        log(f"      stdout (хвост): {stdout}")


def run_subprocess(cmd: list, timeout: int, log=None, cancel_event=None,
                   heartbeat_cb=None) -> subprocess.CompletedProcess:
    try:
        if cancel_event is None:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                creationflags=no_console_creationflags(),
            )
            log_subprocess_result(result, cmd, log=log)
            return result

        started_at = time.monotonic()
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            creationflags=no_console_creationflags(),
        )
        last_heartbeat_at = started_at

        while True:
            if cancel_event.is_set():
                process.kill()
                stdout, stderr = process.communicate()
                raise CancelledError()

            if timeout is not None:
                remaining = timeout - (time.monotonic() - started_at)
                if remaining <= 0:
                    process.kill()
                    stdout, stderr = process.communicate()
                    raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
                wait_time = min(0.25, remaining)
            else:
                wait_time = 0.25

            now = time.monotonic()
            if (log or heartbeat_cb) and now - last_heartbeat_at >= 60:
                executable = os.path.basename(str(cmd[0])) if cmd else "процесс"
                elapsed = int(now - started_at)
                timeout_text = f", лимит {timeout}с" if timeout is not None else ""
                if log:
                    log(f"      ⏳ {executable} продолжает работать: прошло {fmt_time(elapsed)}{timeout_text}")
                if heartbeat_cb:
                    heartbeat_cb(
                        "activity_heartbeat",
                        message="Внешний процесс продолжает выполняться.",
                        operation=executable,
                        elapsed_sec=elapsed,
                        timeout_sec=timeout,
                    )
                last_heartbeat_at = now

            try:
                stdout, stderr = process.communicate(timeout=wait_time)
                result = subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
                log_subprocess_result(result, cmd, log=log)
                return result
            except subprocess.TimeoutExpired:
                continue
    except subprocess.TimeoutExpired as exc:
        if log:
            log(f"      ⚠️ ffmpeg timeout: {timeout}с")
            log(f"      ⚠️ Команда внешнего процесса: {format_subprocess_command(cmd)}")
            stderr = redact_diagnostic_text((getattr(exc, "stderr", "") or "")[-4000:], max_len=4000).strip()
            if stderr:
                log(f"      stderr до timeout (хвост): {stderr}")
        raise exc
