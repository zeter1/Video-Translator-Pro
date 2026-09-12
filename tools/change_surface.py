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

from task_catalog import best_card, tokens


def item_text(item: dict) -> str:
    return " ".join([
        str(item.get("qualified") or ""),
        str(item.get("name") or ""),
        str(item.get("file") or ""),
        str(item.get("doc") or ""),
        " ".join(item.get("calls") or []),
        " ".join(item.get("called_by") or []),
        " ".join(item.get("reads") or []),
        " ".join(item.get("writes") or []),
        " ".join(item.get("constants") or []),
    ]).lower()


def rank_symbols(data: dict, query: str, primary_file: str | None, limit: int = 5) -> list[dict]:
    q = tokens(query)
    ranked = []
    for item in data.get("symbols", []):
        if item.get("kind") == "class" or not item.get("is_primary_owner", True):
            continue
        if primary_file and item.get("file") != primary_file:
            continue
        hay = item_text(item)
        score = sum(hay.count(token) * 2 for token in q)
        for token in q:
            if token == str(item.get("name") or "").lower():
                score += 20
        if score:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].get("lines", 99999)))
    return [item for _, item in ranked[:limit]]


def print_list(label: str, values: list[str], limit: int = 16):
    compact = list(dict.fromkeys(str(value) for value in values if value))[:limit]
    print(f"{label}: " + (", ".join(compact) if compact else "-"))


def main():
    parser = argparse.ArgumentParser(description="Compact change-surface view for one task/symbol.")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--symbol", help="qualified symbol")
    args = parser.parse_args()

    data = json.loads(INDEX.read_text(encoding="utf-8"))
    card = best_card(args.query) if args.query else None
    primary = (card or {}).get("primary")

    if args.symbol:
        symbols = [
            item for item in data.get("symbols", [])
            if str(item.get("qualified") or "").lower() == args.symbol.lower()
            and item.get("is_primary_owner", True)
        ]
    else:
        symbols = []
        for name in (card or {}).get("symbols", []):
            symbols.extend([
                item for item in data.get("symbols", [])
                if item.get("qualified") == name
                and item.get("is_primary_owner", True)
                and (not primary or item.get("file") == primary)
            ])
        if not symbols:
            symbols = rank_symbols(data, args.query, primary)

    print("CHANGE SURFACE")
    if card:
        print(f"CARD: {card['id']} — {card['title']}")
        print(f"PRIMARY: {card['primary']}")
        print_list("SUPPORT", card.get("support") or [])
        print_list("DO NOT READ YET", card.get("avoid") or [])
        print_list("TESTS", card.get("tests") or [], limit=20)

    for item in symbols[:5]:
        print(f"\n{item['qualified']} — {item['file']}:{item['start']}-{item['end']}")
        print_list("READS self", item.get("reads") or [])
        print_list("WRITES self", item.get("writes") or [])
        print_list("CONFIG CONSTANTS", item.get("constants") or [])
        print_list("CALLS", item.get("calls") or [])
        print_list("CALLED BY", item.get("called_by") or [])
        print_list("PATH/FILE LITERALS", item.get("path_literals") or [])


if __name__ == "__main__":
    main()
