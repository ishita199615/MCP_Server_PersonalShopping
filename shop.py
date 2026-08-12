"""Run a shopping list through Target search and print the candidates.

    python shop.py                     # reads shoppinglist.txt
    python shop.py mylist.txt --limit 3

One item per line; blank lines and #comments ignored. Nothing is added to any
cart -- this only searches, so you can eyeball the picks first.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from target_mcp import target
from target_mcp.browser import BrowserSession

DEFAULT_LIST = Path(__file__).resolve().parent / "shoppinglist.txt"


def read_list(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"No such file: {path}")
    # Accept either one-per-line or comma-separated -- people write lists both
    # ways, and "Whole Milk ,Bananas , Apples" should not become one query.
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.extend(part.strip() for part in line.split(",") if part.strip())
    if not items:
        raise SystemExit(f"{path.name} is empty -- put one grocery item per line.")
    return items


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("list_file", nargs="?", default=str(DEFAULT_LIST))
    ap.add_argument("--limit", type=int, default=3)
    args = ap.parse_args()

    items = read_list(Path(args.list_file))
    print(f"{len(items)} items from {Path(args.list_file).name}\n")

    session = BrowserSession()
    try:
        page = await session.page()
        for item in items:
            print(f"== {item} ==")
            try:
                results = await target.search(page, item, args.limit)
            except target.Blocked as exc:
                print(f"   BLOCKED: {exc}\n")
                break
            if not results:
                print("   no results\n")
                continue
            for r in results:
                pickup = r["fulfillment"]["pickup"]
                flag = "pickup" if pickup else ("NO PICKUP" if pickup is False else "?")
                print(
                    f"   {r['item_id']:>10}  {str(r['price_string']):>12}"
                    f"  {str(r['unit_price'] or ''):>7}  {flag:<9}"
                    f"  {(r['name'] or '')[:52]}"
                )
            print()
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
