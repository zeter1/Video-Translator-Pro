from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "videotranslator"
DOCS = ROOT / "docs"
TESTS = ROOT / "tests"
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from task_catalog import TASK_CARDS, legacy_task_routes

PHASE_RE = re.compile(r"^\s*#\s*CODEX-PHASE\s+([A-Z0-9_-]+)\s+([^—-]+?)(?:\s+[—-]\s+(.*))?\s*$")
PATH_LITERAL_RE = re.compile(r"(?:[\\/]|\.jsonl?$|\.wav$|\.mp[34]$|\.txt$|\.md$|\.log$)", re.IGNORECASE)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def doc_summary(node: ast.AST, limit: int = 180) -> str:
    value = ast.get_docstring(node) or ""
    return " ".join(value.split())[:limit]


def module_doc(tree: ast.Module, limit: int = 220) -> str:
    value = ast.get_docstring(tree) or ""
    return " ".join(value.split())[:limit]


def call_names(node: ast.AST) -> list[str]:
    calls = set()
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        fn = item.func
        if isinstance(fn, ast.Name):
            calls.add(fn.id)
        elif isinstance(fn, ast.Attribute):
            if isinstance(fn.value, ast.Name) and fn.value.id == "self":
                calls.add(f"self.{fn.attr}")
            elif isinstance(fn.value, ast.Name):
                calls.add(f"{fn.value.id}.{fn.attr}")
    return sorted(calls)


def attribute_surface(node: ast.AST) -> tuple[list[str], list[str], list[str], list[str]]:
    reads, writes, constants, literals = set(), set(), set(), set()
    for item in ast.walk(node):
        if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id == "self":
            if isinstance(item.ctx, ast.Store):
                writes.add(item.attr)
            elif isinstance(item.ctx, ast.Load):
                reads.add(item.attr)
        elif isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load):
            if item.id.isupper() and len(item.id) > 2:
                constants.add(item.id)
        elif isinstance(item, ast.Constant) and isinstance(item.value, str):
            value = item.value.strip()
            if 2 <= len(value) <= 160 and PATH_LITERAL_RE.search(value):
                literals.add(value)
    return (
        sorted(reads)[:80],
        sorted(writes)[:80],
        sorted(constants)[:80],
        sorted(literals)[:30],
    )


def is_forwarder(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    body = list(node.body)
    if len(body) != 1:
        return False
    expr = body[0].value if isinstance(body[0], (ast.Return, ast.Expr)) else None
    if not isinstance(expr, ast.Call):
        return False
    fn = expr.func
    name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
    if name in {"call_legacy_override", "resolve_legacy_override"}:
        return True
    # A one-line compatibility wrapper that only forwards to a private imported symbol.
    if isinstance(fn, ast.Name) and fn.id.startswith("_legacy_"):
        return True
    return False


def symbol_record(path: Path, node: ast.AST, *, cls_name: str | None = None) -> dict:
    name = node.name
    kind = "method" if cls_name else ("class" if isinstance(node, ast.ClassDef) else "function")
    qualified = f"{cls_name}.{name}" if cls_name else name
    reads, writes, constants, literals = ([], [], [], [])
    calls = []
    forwarder = False
    if not isinstance(node, ast.ClassDef):
        calls = call_names(node)
        reads, writes, constants, literals = attribute_surface(node)
        forwarder = is_forwarder(node)
    return {
        "kind": kind,
        "name": name,
        "qualified": qualified,
        "class": cls_name,
        "file": rel(path),
        "start": node.lineno,
        "end": node.end_lineno,
        "lines": node.end_lineno - node.lineno + 1,
        "doc": doc_summary(node),
        "calls": calls,
        "reads": reads,
        "writes": writes,
        "constants": constants,
        "path_literals": literals,
        "is_forwarder": forwarder,
    }


def choose_primary(records: list[dict]) -> dict:
    # Classes are canonical over same-name compatibility callables.
    # Then prefer real implementations over forwarding shims and larger bodies.
    return max(
        records,
        key=lambda record: (
            1 if record["kind"] == "class" else 0,
            1 if not record.get("is_forwarder") else 0,
            int(record.get("lines") or 0),
            -len(record.get("file") or ""),
        ),
    )


def collect():
    records: list[dict] = []
    modules: list[dict] = []
    source_lines: dict[str, list[str]] = {}

    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        source_lines[rel(path)] = lines
        tree = ast.parse(text, filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                records.append(symbol_record(path, node))
            elif isinstance(node, ast.ClassDef):
                records.append(symbol_record(path, node))
                for method in node.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        records.append(symbol_record(path, method, cls_name=node.name))
        modules.append({
            "file": rel(path),
            "lines": len(lines),
            "doc": module_doc(tree),
        })

    # Resolve canonical ownership before building call graph.
    by_qualified = defaultdict(list)
    by_plain = defaultdict(list)
    for record in records:
        by_qualified[record["qualified"]].append(record)
        by_plain[record["name"]].append(record)

    primary_by_qualified = {}
    for qualified, group in by_qualified.items():
        primary = choose_primary(group)
        primary_by_qualified[qualified] = primary
        for record in group:
            record["primary_owner"] = primary["file"]
            record["is_primary_owner"] = record is primary
            if record is not primary:
                record["forwarded_to"] = primary["qualified"]

    # Resolve calls to one canonical target where possible. This avoids v3 ambiguity
    # where one wrapper name appeared to have several equal "owners".
    called_by = defaultdict(set)
    for caller in records:
        for called in caller.get("calls", []):
            plain = called[5:] if called.startswith("self.") else called.split(".")[-1]
            candidates = by_plain.get(plain, [])
            if not candidates:
                continue
            # If a same-class method exists, it is the strongest target.
            same_class = [
                item for item in candidates
                if caller.get("class") and item.get("class") == caller.get("class")
            ]
            pool = same_class or candidates
            canonical_groups = {}
            for item in pool:
                canonical_groups[item["qualified"]] = primary_by_qualified[item["qualified"]]
            canonical = list(canonical_groups.values())
            if len(canonical) == 1:
                target = canonical[0]
                called_by[(target["qualified"], target["file"])].add(
                    f"{caller['qualified']}@{caller['file']}"
                )

    for record in records:
        primary = primary_by_qualified[record["qualified"]]
        record["called_by"] = sorted(
            called_by.get((primary["qualified"], primary["file"]), set())
        )[:80]

    phases = []
    by_file_symbols = defaultdict(list)
    for record in records:
        if record["kind"] in {"function", "method"}:
            by_file_symbols[record["file"]].append(record)

    for file_name, lines in source_lines.items():
        raw = []
        for number, line in enumerate(lines, 1):
            match = PHASE_RE.match(line)
            if not match:
                continue
            owner_candidates = [
                record for record in by_file_symbols[file_name]
                if record["start"] <= number <= record["end"]
            ]
            owner = min(owner_candidates, key=lambda record: record["lines"], default=None)
            raw.append({
                "id": match.group(1).strip(),
                "name": match.group(2).strip(),
                "description": (match.group(3) or "").strip(),
                "file": file_name,
                "start": number,
                "owner": owner["qualified"] if owner else "",
                "owner_end": owner["end"] if owner else len(lines),
            })
        for index, phase in enumerate(raw):
            next_start = raw[index + 1]["start"] if index + 1 < len(raw) else phase["owner_end"] + 1
            end = min(phase["owner_end"], next_start - 1)
            phase["end"] = max(phase["start"], end)
            phase["lines"] = phase["end"] - phase["start"] + 1
            phase.pop("owner_end", None)
            phases.append(phase)

    duplicate_groups = []
    for qualified, group in sorted(by_qualified.items()):
        if len(group) <= 1:
            continue
        primary = primary_by_qualified[qualified]
        duplicate_groups.append({
            "qualified": qualified,
            "primary": primary["file"],
            "alternates": [
                {
                    "file": item["file"],
                    "lines": item["lines"],
                    "is_forwarder": bool(item.get("is_forwarder")),
                }
                for item in group if item is not primary
            ],
        })

    return records, modules, phases, duplicate_groups


def collect_tests() -> list[dict]:
    records = []
    if not TESTS.exists():
        return records
    for path in sorted(TESTS.rglob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                records.append({
                    "name": node.name,
                    "qualified": node.name,
                    "file": rel(path),
                    "start": node.lineno,
                    "end": node.end_lineno,
                    "lines": node.end_lineno - node.lineno + 1,
                    "calls": call_names(node),
                })
            elif isinstance(node, ast.ClassDef):
                for method in node.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test_"):
                        records.append({
                            "name": method.name,
                            "qualified": f"{node.name}.{method.name}",
                            "file": rel(path),
                            "start": method.lineno,
                            "end": method.end_lineno,
                            "lines": method.end_lineno - method.lineno + 1,
                            "calls": call_names(method),
                        })
    return records


def render_markdown(phases: list[dict]) -> str:
    lines = [
        "# CODE INDEX — tiny fallback",
        "",
        "> Normal Codex path: `python tools/find_owner.py \"task\"` → `python tools/codex_context.py \"task\" --code`.",
        "> Do **not** read this file or `CODE_MAP.json` for a normal local change.",
        "",
        "## Task cards",
        "",
        "| ID | Primary owner | Support | Budget |",
        "|---|---|---|---:|",
    ]
    for card in TASK_CARDS:
        support = ", ".join(f"`{item}`" for item in card.get("support", [])[:2]) or "—"
        lines.append(
            f"| `{card['id']}` | `{card['primary']}` | {support} | {card.get('default_lines', 100)} |"
        )
    lines += [
        "",
        "## Long workflow phases",
        "",
        "| Phase | Exact owner/range |",
        "|---|---|",
    ]
    for phase in phases:
        lines.append(
            f"| `{phase['id']} {phase['name']}` | `{phase['file']}:{phase['start']}–{phase['end']}` |"
        )
    lines += [
        "",
        "Detailed symbols, callers, state reads/writes and test metadata live only in the machine index.",
        "",
    ]
    return "\n".join(lines)


def render_task_cards_markdown() -> str:
    lines = [
        "# TASK CARDS — deterministic Codex router",
        "",
        "This is a human fallback. The tools consume the same cards directly from `tools/task_catalog.py`.",
        "",
        "| ID | Intent | Primary | Support | Tests |",
        "|---|---|---|---|---:|",
    ]
    for card in TASK_CARDS:
        support = ", ".join(f"`{item}`" for item in card.get("support", [])[:2]) or "—"
        lines.append(
            f"| `{card['id']}` | {card['title']} | `{card['primary']}` | {support} | {len(card.get('tests', []))} |"
        )
    lines += [
        "",
        "Each card also defines aliases, `DO NOT READ YET`, preferred symbols/phases, exact test selectors and a source-line budget.",
        "",
    ]
    return "\n".join(lines)


def main():
    DOCS.mkdir(exist_ok=True)
    records, modules, phases, duplicate_groups = collect()
    tests = collect_tests()
    payload = {
        "schema_version": 3,
        "generated_from": "videotranslator/**/*.py",
        "task_routes": legacy_task_routes(),
        "task_cards": TASK_CARDS,
        "modules": modules,
        "phases": phases,
        "symbols": records,
        "duplicate_symbols": duplicate_groups,
        "tests": tests,
    }
    (DOCS / "CODE_MAP.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (DOCS / "CODE_INDEX.md").write_text(render_markdown(phases), encoding="utf-8")
    (DOCS / "TASK_CARDS.md").write_text(render_task_cards_markdown(), encoding="utf-8")
    print(
        f"indexed {len(records)} symbols, {len(modules)} modules, "
        f"{len(phases)} phases, {len(tests)} tests, "
        f"{len(duplicate_groups)} duplicate symbol groups"
    )


if __name__ == "__main__":
    main()
