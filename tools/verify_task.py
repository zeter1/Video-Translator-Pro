from __future__ import annotations

import argparse
import ast
import compileall
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TESTS_DIR = ROOT / "tests"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from task_catalog import best_card


def _test_class_index() -> dict[str, Path]:
    """Map unittest class names to files without importing the entire suite."""
    owners: dict[str, Path] = {}
    duplicates: dict[str, list[Path]] = {}
    for test_file in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(test_file.read_text(encoding="utf-8"), filename=str(test_file))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name in owners and owners[node.name] != test_file:
                duplicates.setdefault(node.name, [owners[node.name]]).append(test_file)
            else:
                owners[node.name] = test_file
    if duplicates:
        detail = "; ".join(
            f"{name}: {', '.join(str(path.relative_to(ROOT)) for path in paths)}"
            for name, paths in sorted(duplicates.items())
        )
        raise RuntimeError(f"ambiguous targeted test class names: {detail}")
    return owners


def load_test_module(test_file: Path):
    module_name = f"vt_targeted_{test_file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, test_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {test_file}")
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
    class_index = _test_class_index()
    module_cache = {}
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for selector in selectors:
        class_name = selector.split(".", 1)[0]
        test_file = class_index.get(class_name)
        if test_file is None:
            raise RuntimeError(f"targeted test class not found: {class_name}")
        module = module_cache.get(test_file)
        if module is None:
            module = load_test_module(test_file)
            module_cache[test_file] = module
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
