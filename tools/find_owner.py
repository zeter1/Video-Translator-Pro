from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
INDEX = ROOT / "docs" / "CODE_MAP.json"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from task_catalog import best_card, best_phases, rank_cards, tokens


def item_text(item: dict) -> str:
    return " ".join([
        str(item.get("qualified") or ""),
        str(item.get("name") or ""),
        str(item.get("doc") or ""),
        str(item.get("file") or ""),
        " ".join(item.get("calls") or []),
        " ".join(item.get("called_by") or []),
        " ".join(item.get("constants") or []),
    ]).lower()


def fallback_symbols(data: dict, query: str, limit: int = 5) -> list[tuple[int, dict]]:
    q = tokens(query)
    ranked = []
    for item in data.get("symbols", []):
        if item.get("kind") == "class":
            continue
        if not item.get("is_primary_owner", True):
            continue
        hay = item_text(item)
        score = 0
        for token in q:
            score += hay.count(token) * 2
            if token == str(item.get("name") or "").lower():
                score += 20
            if token in str(item.get("file") or "").lower():
                score += 4
        if score:
            ranked.append((score, item))
    return sorted(
        ranked,
        key=lambda pair: (-pair[0], pair[1].get("lines", 99999), pair[1].get("file", "")),
    )[:limit]


def main():
    parser = argparse.ArgumentParser(description="Deterministic task → owner router for Codex.")
    parser.add_argument("query", nargs="+", help="symptom or requested change")
    parser.add_argument("--alternatives", action="store_true", help="show secondary task-card candidates")
    args = parser.parse_args()

    if not INDEX.exists():
        raise SystemExit("CODE_MAP.json missing; run python tools/generate_code_index.py")

    query = " ".join(args.query)
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    card = best_card(query)

    print(f"QUERY: {query}")

    if card:
        print(f"\nTASK CARD: {card['id']} — {card['title']}")
        print(f"PRIMARY: {card['primary']}")
        support = card.get("support") or []
        if support:
            print("SUPPORT (open only if primary evidence crosses boundary):")
            for path in support:
                print(f"  - {path}")
        phases = best_phases(query, card, limit=2)
        if phases:
            phase_index = {item["id"]: item for item in data.get("phases", [])}
            print("PREFERRED PHASE:")
            for phase_id in phases:
                item = phase_index.get(phase_id)
                if item:
                    print(
                        f"  - {phase_id} {item['name']}: "
                        f"{item['file']}:{item['start']}-{item['end']}"
                    )
        symbols = card.get("symbols") or []
        if symbols:
            print("PREFERRED SYMBOLS:")
            by_qualified = {}
            for item in data.get("symbols", []):
                if item.get("is_primary_owner", True):
                    by_qualified.setdefault(item.get("qualified"), item)
            for name in symbols[:5]:
                item = by_qualified.get(name)
                if item:
                    print(f"  - {name}: {item['file']}:{item['start']}-{item['end']}")
                else:
                    print(f"  - {name}")
        avoid = card.get("avoid") or []
        if avoid:
            print("DO NOT READ YET:")
            for path in avoid:
                print(f"  - {path}")
        print(f"SOURCE BUDGET: {int(card.get('default_lines') or 100)} lines")
        tests = card.get("tests") or []
        if tests:
            print("TARGETED TESTS:")
            for name in tests:
                print(f"  - {name}")
        else:
            print("TARGETED TESTS: none mapped; use static checks + full regression if behavior changed.")

        if args.alternatives:
            ranked = rank_cards(query)
            others = [(score, item) for score, item in ranked if item["id"] != card["id"]][:3]
            if others:
                print("\nALTERNATIVE CARDS:")
                for score, item in others:
                    print(f"  {score:>4}  {item['id']}: {item['primary']}")
    else:
        print("\nTASK CARD: no deterministic match; falling back to canonical symbols.")
        symbols = fallback_symbols(data, query)
        if symbols:
            print("SYMBOLS:")
            for score, item in symbols:
                print(
                    f"  {score:>3}  {item['qualified']}: "
                    f"{item['file']}:{item['start']}-{item['end']}"
                )
        else:
            print("No owner found. Use a more specific symptom or symbol name.")

    escaped = query.replace('"', '\\"')
    print(f'\nNEXT: python tools/codex_context.py "{escaped}" --code')


if __name__ == "__main__":
    main()
