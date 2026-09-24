from __future__ import annotations

import compileall
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(label, fn):
    try:
        fn()
        print(f"PASS  {label}")
        return True
    except Exception as exc:
        print(f"FAIL  {label}: {type(exc).__name__}: {exc}")
        return False


def command(*parts: str, no_site: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    argv = [sys.executable]
    if no_site:
        argv.append("-S")
    argv.extend(parts)
    return subprocess.run(
        argv,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def check_compile():
    if not compileall.compile_dir(ROOT / "videotranslator", quiet=1, force=True):
        raise RuntimeError("compileall failed")
    if not compileall.compile_file(ROOT / "video_translator.py", quiet=1, force=True):
        raise RuntimeError("launcher compile failed")
    for tool in (ROOT / "tools").glob("*.py"):
        if not compileall.compile_file(tool, quiet=1, force=True):
            raise RuntimeError(f"tool compile failed: {tool.name}")


def check_import():
    sys.path.insert(0, str(ROOT))
    import video_translator as vt

    required = [
        "App", "VideoTranslator", "ProblemLogger", "TTSCache",
        "assemble_final_video", "run_subprocess", "get_whisper_model",
        "translation_checkpoint_path", "get_batch_recovery_state_path",
        "reconstruct_batch_recovery_state_from_problem_log",
    ]
    missing = [name for name in required if not hasattr(vt, name)]
    if missing:
        raise RuntimeError(f"public API missing: {missing}")
    if Path(vt.get_program_dir()).resolve() != ROOT.resolve():
        raise RuntimeError(f"program dir drifted: {vt.get_program_dir()} != {ROOT}")
    if vt.reconstruct_batch_recovery_state_from_problem_log("") != {}:
        raise RuntimeError("empty batch problem-log path must be a safe no-op")
    if vt.reconstruct_batch_recovery_state_from_problem_log(Path(".")) != {}:
        raise RuntimeError("directory batch problem-log path must be a safe no-op")


def check_index():
    result = command(str(ROOT / "tools" / "generate_code_index.py"))
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    data = json.loads((ROOT / "docs" / "CODE_MAP.json").read_text(encoding="utf-8"))
    if int(data.get("schema_version", 0)) < 3:
        raise RuntimeError("CODE_MAP schema is stale")
    if len(data.get("task_cards", [])) < 20:
        raise RuntimeError("task card catalog unexpectedly small")
    if len(data.get("symbols", [])) < 150:
        raise RuntimeError("code map unexpectedly small")
    phase_ids = {item.get("id") for item in data.get("phases", [])}
    for required in {"VT3", "TL3", "BW2", "FM4"}:
        if required not in phase_ids:
            raise RuntimeError(f"missing CODEX phase: {required}")

    # Every duplicate qualified name must have one and only one canonical owner.
    groups = defaultdict(list)
    for item in data.get("symbols", []):
        groups[item.get("qualified")].append(item)
    bad = []
    for name, items in groups.items():
        if len(items) <= 1:
            continue
        primaries = [item for item in items if item.get("is_primary_owner")]
        if len(primaries) != 1:
            bad.append((name, len(primaries)))
    if bad:
        raise RuntimeError(f"ambiguous canonical ownership remains: {bad[:10]}")


def check_navigation_harness():
    owner = command(str(ROOT / "tools" / "find_owner.py"), "batch recovery empty path")
    if owner.returncode:
        raise RuntimeError(owner.stderr or owner.stdout)
    if "TASK CARD: batch_recovery_state" not in owner.stdout:
        raise RuntimeError("batch recovery card was not selected")
    if "PRIMARY: videotranslator/recovery/batch.py" not in owner.stdout:
        raise RuntimeError("batch recovery primary owner is wrong")

    context = command(
        str(ROOT / "tools" / "codex_context.py"),
        "final mp4 nvenc timeout",
        "--code",
    )
    if context.returncode:
        raise RuntimeError(context.stderr or context.stdout)
    if "CARD: final_mp4" not in context.stdout:
        raise RuntimeError("final MP4 card was not selected")
    if "PRIMARY: videotranslator/media/final_video.py" not in context.stdout:
        raise RuntimeError("final MP4 primary owner is wrong")
    used = None
    for line in context.stdout.splitlines():
        if line.startswith("SOURCE LINES USED:"):
            used = int(line.split(":", 1)[1].split("/", 1)[0].strip())
    if used is None or used > 120:
        raise RuntimeError(f"context budget failed: {used}")

    surface = command(
        str(ROOT / "tools" / "change_surface.py"),
        "translation checkpoint resume incomplete text",
    )
    if surface.returncode or "WRITES self" not in surface.stdout:
        raise RuntimeError(surface.stderr or "change-surface output incomplete")

    targeted = command(
        str(ROOT / "tools" / "verify_task.py"),
        "batch recovery empty path",
        no_site=False,
    )
    if targeted.returncode:
        raise RuntimeError((targeted.stdout + "\n" + targeted.stderr)[-8000:])
    if "targeted tests (3)" not in targeted.stdout:
        raise RuntimeError("targeted verification router did not run expected 3 tests")


def check_context_benchmark():
    result = command(str(ROOT / "tools" / "context_benchmark.py"))
    if result.returncode:
        raise RuntimeError((result.stdout + "\n" + result.stderr)[-8000:])
    benchmark = (ROOT / "docs" / "CONTEXT_BENCHMARK.md").read_text(encoding="utf-8")
    if "Result: PASS" not in benchmark:
        raise RuntimeError("context benchmark did not pass")


def check_distribution_contract():
    required = [
        ROOT / "Запустить.bat",
        ROOT / "Установить_зависимости.bat",
        ROOT / "build_exe.bat",
        ROOT / "tools" / "build_exe.py",
        ROOT / "tools" / "ensure_ffmpeg_windows.py",
        ROOT / "pytest.ini",
        ROOT / "requirements.txt",
        ROOT / "requirements-optional-ai.txt",
    ]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"distribution files missing: {missing}")

    pytest_config = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    if "testpaths = tests" not in pytest_config or "repair_evidence" not in pytest_config:
        raise RuntimeError("pytest discovery is not isolated from historical repair evidence")

    for tool in ("build_exe.py", "ensure_ffmpeg_windows.py"):
        help_result = command(str(ROOT / "tools" / tool), "--help")
        if help_result.returncode:
            raise RuntimeError(help_result.stderr or help_result.stdout)


def check_tests():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError((result.stdout + "\n" + result.stderr)[-12000:])
    combined = result.stdout + result.stderr
    count = re.search(r"Ran (\d+) tests?", combined)
    if count is None or int(count.group(1)) < 43:
        raise RuntimeError("regression suite did not run the minimum baseline of 43 tests")
    print(combined.strip())


def main():
    checks = [
        ("compile", check_compile),
        ("legacy import/public API + recovery guard", check_import),
        ("CODE_MAP v3 + canonical ownership", check_index),
        ("deterministic task cards + targeted verification", check_navigation_harness),
        ("context benchmark", check_context_benchmark),
        ("Windows distribution/build contract", check_distribution_contract),
        ("complete regression suite (at least 43 tests)", check_tests),
    ]
    ok = all(run(label, fn) for label, fn in checks)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
