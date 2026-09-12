from __future__ import annotations

import argparse
import compileall
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TEST_FILE = ROOT / "tests" / "test_video_translator_diagnostics.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from task_catalog import best_card


def load_test_module():
    spec = importlib.util.spec_from_file_location("vt_targeted_tests", TEST_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TEST_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def static_checks() -> None:
    if not compileall.compile_dir(ROOT / "videotranslator", quiet=1, force=True):
        raise RuntimeError("package compile failed")
    if not compileall.compile_file(ROOT / "video_translator.py", quiet=1, force=True):
        raise RuntimeError("launcher compile failed")
    import video_translator as vt
    if Path(vt.get_program_dir()).resolve() != ROOT.resolve():
        raise RuntimeError("program directory drifted after refactor")


def run_selectors(selectors: list[str]) -> tuple[bool, int]:
    if not selectors:
        return True, 0
    module = load_test_module()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for selector in selectors:
        suite.addTests(loader.loadTestsFromName(selector, module))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful(), result.testsRun


def run_full() -> bool:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return result.wasSuccessful()


def main():
    parser = argparse.ArgumentParser(description="Run the smallest mapped verification set for one Codex task.")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--full", action="store_true", help="run full regression after targeted checks")
    parser.add_argument("--card", help="force task-card id")
    args = parser.parse_args()

    static_checks()
    print("PASS  static/import")

    card = best_card(args.card or args.query)
    if args.card:
        # best_card accepts exact card id with very high score.
        card = best_card(args.card)
    if not card:
        print("NO TASK CARD: no targeted selectors; run --full for behavioral verification.")
        if args.full and not run_full():
            raise SystemExit(1)
        return

    print(f"CARD  {card['id']} — {card['title']}")
    selectors = list(card.get("tests") or [])
    if selectors:
        ok, count = run_selectors(selectors)
        print(f"{'PASS' if ok else 'FAIL'}  targeted tests ({count})")
        if not ok:
            raise SystemExit(1)
    else:
        print("INFO  no exact targeted regression selectors are mapped for this card.")

    if args.full:
        if not run_full():
            raise SystemExit(1)
        print("PASS  full regression")


if __name__ == "__main__":
    main()
