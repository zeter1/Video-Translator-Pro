from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_TOOL = ROOT / "tools" / "codex_context.py"
INDEX = ROOT / "docs" / "CODE_MAP.json"
OUT = ROOT / "docs" / "CONTEXT_BENCHMARK.md"

CASES = [
    ("batch recovery empty path", "batch_recovery_state", "videotranslator/recovery/batch.py"),
    ("restore previous batch on startup", "startup_recovery_offer", "videotranslator/ui/batch_recovery.py"),
    ("translation checkpoint resume incomplete text", "translation_checkpoint", "videotranslator/recovery/translation.py"),
    ("google translator retry failed segment", "translation_requests", "videotranslator/pipeline/translation.py"),
    ("edge tts vpn fallback selected voice", "edge_tts_network", "videotranslator/pipeline/tts_generate.py"),
    ("pause sync speech boundary timeline", "pause_sync", "videotranslator/pipeline/timeline_build.py"),
    ("final mp4 nvenc timeout", "final_mp4", "videotranslator/media/final_video.py"),
    ("ffmpeg subprocess timeout cancel", "ffmpeg_process", "videotranslator/media/process.py"),
    ("tts cache cleanup size recovery", "tts_cache", "videotranslator/tts/cache.py"),
    ("diagnostic report manifest markdown", "diagnostics_report", "videotranslator/diagnostics/report_render.py"),
    ("batch worker multiple files progress", "batch_worker", "videotranslator/ui/batch_worker.py"),
    ("whisper gpu cuda model", "whisper", "videotranslator/speech/whisper.py"),
]

SOURCE_RE = re.compile(r"^\s*\d+:\s", re.MULTILINE)


def main():
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    repo_loc = sum(
        int(item.get("lines") or 0)
        for item in data.get("modules", [])
    )
    rows = []
    failures = []

    for query, expected_card, expected_primary in CASES:
        result = subprocess.run(
            [sys.executable, "-S", str(CONTEXT_TOOL), query, "--code"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            failures.append(f"{query}: tool failed")
            continue
        output = result.stdout
        card_match = re.search(r"^CARD:\s+(\S+)", output, re.MULTILINE)
        primary_match = re.search(r"^PRIMARY:\s+(.+)$", output, re.MULTILINE)
        used_match = re.search(r"SOURCE LINES USED:\s+(\d+)/(\d+)", output)
        card = card_match.group(1) if card_match else ""
        primary = primary_match.group(1).strip() if primary_match else ""
        used = int(used_match.group(1)) if used_match else len(SOURCE_RE.findall(output))
        budget = int(used_match.group(2)) if used_match else 0

        if card != expected_card:
            failures.append(f"{query}: card={card!r}, expected={expected_card!r}")
        if primary != expected_primary:
            failures.append(f"{query}: primary={primary!r}, expected={expected_primary!r}")
        if budget and used > budget:
            failures.append(f"{query}: source budget exceeded {used}>{budget}")

        reduction = (1.0 - used / repo_loc) * 100 if repo_loc else 0.0
        rows.append((query, card, primary, used, budget, reduction))

    lines = [
        "# CONTEXT BENCHMARK",
        "",
        "Representative Codex tasks routed through `tools/codex_context.py --code`.",
        f"Python package size used as comparison baseline: **{repo_loc} source lines**.",
        "",
        "| Task | Card | Primary | Source lines | Budget | Reduction vs package |",
        "|---|---|---|---:|---:|---:|",
    ]
    for query, card, primary, used, budget, reduction in rows:
        lines.append(
            f"| {query} | `{card}` | `{primary}` | {used} | {budget} | {reduction:.1f}% |"
        )
    if failures:
        lines += ["", "## Failures", ""] + [f"- {item}" for item in failures]
    else:
        lines += ["", "**Result: PASS — deterministic routing and hard source budgets held for every benchmark case.**", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"benchmark: {len(rows)} cases, repo_loc={repo_loc}, failures={len(failures)}")
    if failures:
        for item in failures:
            print("FAIL", item)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
