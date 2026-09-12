from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
INDEX = ROOT / "docs" / "CODE_MAP.json"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from task_catalog import best_card, best_phases, tokens


def read_range(file_name: str, start: int, end: int, max_lines: int) -> str:
    path = ROOT / file_name
    lines = path.read_text(encoding="utf-8").splitlines()
    start = max(1, int(start))
    end = min(len(lines), int(end))
    if end - start + 1 > max_lines:
        end = start + max_lines - 1
    return "\n".join(f"{i:>5}: {lines[i - 1]}" for i in range(start, end + 1))


def item_text(item: dict) -> str:
    return " ".join([
        str(item.get("qualified") or ""),
        str(item.get("name") or ""),
        str(item.get("doc") or ""),
        str(item.get("file") or ""),
        " ".join(item.get("calls") or []),
        " ".join(item.get("called_by") or []),
        " ".join(item.get("constants") or []),
        " ".join(item.get("reads") or []),
        " ".join(item.get("writes") or []),
    ]).lower()


def rank_primary_symbols(data: dict, query: str, file_name: str, limit: int = 4) -> list[dict]:
    q = tokens(query)
    ranked = []
    for item in data.get("symbols", []):
        if item.get("file") != file_name:
            continue
        if not item.get("is_primary_owner", True):
            continue
        if item.get("kind") == "class":
            continue
        hay = item_text(item)
        value = 0
        for token in q:
            value += hay.count(token) * 2
            if token == str(item.get("name") or "").lower():
                value += 20
        # Smaller leaf symbols are better context than a giant orchestration body,
        # unless the query explicitly names the orchestration method.
        if value:
            value += max(0, 12 - min(12, int(item.get("lines") or 0) // 20))
            ranked.append((value, item))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].get("lines", 99999), pair[1].get("start", 0)))
    return [item for _, item in ranked[:limit]]


def find_symbol(data: dict, qualified: str, *, primary_file: str | None = None) -> dict | None:
    candidates = [
        item for item in data.get("symbols", [])
        if str(item.get("qualified") or "").lower() == qualified.lower()
        and item.get("is_primary_owner", True)
    ]
    if primary_file:
        in_file = [item for item in candidates if item.get("file") == primary_file]
        if in_file:
            candidates = in_file
    if not candidates:
        return None
    return min(candidates, key=lambda item: (0 if item.get("kind") != "class" else 1, item.get("lines", 99999)))


def find_phase(data: dict, phase_id: str) -> dict | None:
    for item in data.get("phases", []):
        if str(item.get("id") or "").lower() == phase_id.lower():
            return item
    return None


def test_records(data: dict, selectors: list[str]) -> list[dict]:
    by_name = {item.get("qualified"): item for item in data.get("tests", [])}
    return [by_name[name] for name in selectors if name in by_name]


def fallback_context(data: dict, query: str) -> tuple[dict | None, list[tuple[str, dict]]]:
    q = tokens(query)
    ranked = []
    for item in data.get("symbols", []):
        if item.get("kind") == "class" or not item.get("is_primary_owner", True):
            continue
        hay = item_text(item)
        value = sum(hay.count(token) * 2 for token in q)
        for token in q:
            if token == str(item.get("name") or "").lower():
                value += 20
        if value:
            ranked.append((value, item))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].get("lines", 99999), pair[1].get("file", "")))
    return None, [("symbol", item) for _, item in ranked[:2]]


def compact_surface(item: dict) -> str:
    reads = ", ".join(item.get("reads", [])[:12]) or "-"
    writes = ", ".join(item.get("writes", [])[:12]) or "-"
    constants = ", ".join(item.get("constants", [])[:10]) or "-"
    calls = ", ".join(item.get("calls", [])[:10]) or "-"
    callers = ", ".join(item.get("called_by", [])[:6]) or "-"
    return (
        f"  reads: {reads}\n"
        f"  writes: {writes}\n"
        f"  constants: {constants}\n"
        f"  calls: {calls}\n"
        f"  called_by: {callers}"
    )


def main():
    parser = argparse.ArgumentParser(description="Print a deterministic, token-bounded Codex task pack.")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--code", action="store_true", help="include primary source only")
    parser.add_argument("--support-code", action="store_true", help="also allow support-owner source")
    parser.add_argument("--phase", help="force one CODEX-PHASE")
    parser.add_argument("--symbol", help="force one qualified symbol")
    parser.add_argument("--max-lines", type=int, default=None, help="override task-card source line budget")
    parser.add_argument("--surface", action="store_true", help="show compact state/call surface for selected symbols")
    args = parser.parse_args()

    if not INDEX.exists():
        raise SystemExit("CODE_MAP.json missing; run python tools/generate_code_index.py")
    data = json.loads(INDEX.read_text(encoding="utf-8"))

    card = best_card(args.query) if args.query else None
    default_budget = int((card or {}).get("default_lines") or 100)
    budget = max(20, int(args.max_lines or default_budget))

    print("CODEX TASK PACK")
    print(f"TASK: {args.query or '(forced selection)'}")
    print("PROTOCOL: primary first → support only on evidence → targeted verify → full regression when needed.")

    if card:
        print(f"\nCARD: {card['id']} — {card['title']}")
        print(f"PRIMARY: {card['primary']}")
        support = card.get("support") or []
        print("SUPPORT: " + (", ".join(support) if support else "-"))
        avoid = card.get("avoid") or []
        if avoid:
            print("DO NOT READ YET: " + ", ".join(avoid))
        print(f"SOURCE BUDGET: {budget} lines")

    selected: list[tuple[str, dict]] = []
    seen = set()

    def add(kind: str, item: dict | None):
        if not item:
            return
        key = (item.get("file"), item.get("start"), item.get("end"))
        if key in seen:
            return
        selected.append((kind, item))
        seen.add(key)

    if args.phase:
        add("phase", find_phase(data, args.phase))
    if args.symbol:
        add("symbol", find_symbol(data, args.symbol, primary_file=(card or {}).get("primary")))

    if not args.phase and not args.symbol and card:
        primary = card["primary"]
        chosen_phases = best_phases(args.query, card, limit=2)
        # Only primary-owner phases enter source by default. Support phases are references.
        primary_phases = []
        support_phases = []
        for phase_id in chosen_phases:
            phase = find_phase(data, phase_id)
            if not phase:
                continue
            if phase.get("file") == primary:
                primary_phases.append(phase)
            else:
                support_phases.append(phase)

        for phase in primary_phases[:1]:
            add("phase", phase)

        # Preferred leaf symbols from the card. Suppress a giant symbol if a selected
        # phase already gives the exact slice inside it.
        selected_phase_owners = {item.get("owner") for kind, item in selected if kind == "phase"}
        for qualified in card.get("symbols", []):
            item = find_symbol(data, qualified, primary_file=primary)
            if not item:
                continue
            if item.get("qualified") in selected_phase_owners:
                continue
            add("symbol", item)
            if len(selected) >= 4:
                break

        if not selected:
            for item in rank_primary_symbols(data, args.query, primary, limit=3):
                add("symbol", item)

        if support_phases:
            print("\nLINKED SUPPORT PHASES (do not open unless primary points there):")
            for phase in support_phases:
                print(f"- {phase['id']} {phase['name']}: {phase['file']}:{phase['start']}-{phase['end']}")

    if not selected:
        _, fallback = fallback_context(data, args.query)
        for kind, item in fallback:
            add(kind, item)

    print("\nPRIMARY TARGETS:")
    if not selected:
        print("- none")
    for kind, item in selected:
        if kind == "phase":
            print(
                f"- PHASE {item['id']} {item['name']}: "
                f"{item['file']}:{item['start']}-{item['end']} owner={item.get('owner') or '-'}"
            )
        else:
            print(
                f"- {item['qualified']}: {item['file']}:{item['start']}-{item['end']} "
                f"({item['lines']} lines)"
            )
            if args.surface:
                print(compact_surface(item))

    if card:
        tests = test_records(data, card.get("tests", []))
        print("\nTARGETED VERIFICATION:")
        if tests:
            for item in tests:
                print(f"- {item['qualified']}: {item['file']}:{item['start']}-{item['end']}")
            escaped = args.query.replace('"', '\\"')
            print(f'- run: python tools/verify_task.py "{escaped}"')
        else:
            print("- no exact regression selector mapped")
            print("- run static/import checks; use full suite if behavior changed")

    if not args.code:
        print("\nNo source printed. Re-run with --code after the route is correct.")
        return

    code_targets = list(selected)

    if args.support_code and card:
        primary_file = card["primary"]
        for support_file in card.get("support", [])[:2]:
            for item in rank_primary_symbols(data, args.query, support_file, limit=1):
                if item.get("file") != primary_file:
                    add("support", item)
        code_targets = list(selected)

    remaining = budget
    print(f"\nSOURCE EXCERPTS (hard budget={budget} lines):")
    for kind, item in code_targets:
        if remaining <= 0:
            break
        length = int(item["end"]) - int(item["start"]) + 1
        allocation = min(length, remaining)
        label = (
            f"PHASE {item['id']} {item['name']}"
            if kind == "phase"
            else item.get("qualified") or "symbol"
        )
        print(f"\n### {label} — {item['file']}:{item['start']}-{item['end']}")
        print(read_range(item["file"], item["start"], item["end"], allocation))
        if allocation < length:
            print(f"... truncated: {length - allocation} lines remain; open only this exact range if required")
        remaining -= allocation

    printed = budget - remaining
    print(f"\nSOURCE LINES USED: {printed}/{budget}")


if __name__ == "__main__":
    main()
