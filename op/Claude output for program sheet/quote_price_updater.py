"""
quote_price_updater.py
---------------------
Controlled updater for quote_price_book.json.

This script is intentionally conservative:
  - manual overrides always work
  - web fetches are opt-in and driven by a separate sources JSON
  - fetched values are written back with source metadata and timestamps

Examples
--------
python quote_price_updater.py --set materials.mild_steel.price_per_kg=82
python quote_price_updater.py --sources quote_price_sources.json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PRICE_BOOK = os.path.join(SCRIPT_DIR, "quote_price_book.json")


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8-sig") as f:
        json.dump(data, f, indent=2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _coerce_scalar(value: str):
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _set_nested(data: dict, path: str, value) -> None:
    parts = path.split(".")
    cursor = data
    for key in parts[:-1]:
        if key not in cursor or not isinstance(cursor[key], dict):
            cursor[key] = {}
        cursor = cursor[key]
    cursor[parts[-1]] = value


def _fetch_url(url: str, timeout_s: int = 20) -> str:
    req = Request(url, headers={"User-Agent": "insta-quote-updater/1.0"})
    with urlopen(req, timeout=timeout_s) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _json_path_get(data, path: str):
    cursor = data
    for token in path.split("."):
        if isinstance(cursor, list):
            cursor = cursor[int(token)]
        else:
            cursor = cursor[token]
    return cursor


def _apply_source(book: dict, source: dict) -> dict:
    source_type = source.get("type")
    target = source.get("target")
    if not source_type or not target:
        raise ValueError("Each source needs 'type' and 'target'.")

    value = None
    meta_source = source.get("url", "manual_source")
    if source_type == "json_api":
        payload = _fetch_url(source["url"])
        parsed = json.loads(payload)
        value = _json_path_get(parsed, source["json_path"])
    elif source_type == "html_regex":
        payload = _fetch_url(source["url"])
        match = re.search(source["pattern"], payload, flags=re.IGNORECASE | re.MULTILINE)
        if not match:
            raise ValueError(f"Pattern not found for source target {target}")
        value = match.group(1)
    elif source_type == "static":
        value = source["value"]
        meta_source = source.get("source", "static")
    else:
        raise ValueError(f"Unsupported source type: {source_type}")

    value = _coerce_scalar(str(value))
    _set_nested(book, target, value)
    parent_path = ".".join(target.split(".")[:-1])
    if parent_path:
        parent = book
        for key in parent_path.split("."):
            parent = parent[key]
        if isinstance(parent, dict):
            parent["source"] = meta_source
            parent["updated_at"] = _now_iso()
    return {
        "target": target,
        "value": value,
        "source": meta_source,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Update quote price book with manual and optional web-driven values.")
    parser.add_argument("--price-book", default=DEFAULT_PRICE_BOOK, help="Path to quote_price_book.json")
    parser.add_argument("--set", action="append", default=[],
                        help="Manual override in dotted.path=value form. Can be repeated.")
    parser.add_argument("--sources", default=None,
                        help="Optional JSON file describing fetch sources. Useful for scheduled refresh jobs.")
    args = parser.parse_args()

    if not os.path.exists(args.price_book):
        print(f"ERROR: price book not found: {args.price_book}")
        sys.exit(1)

    book = _load_json(args.price_book)
    changes = []

    for entry in args.set:
        if "=" not in entry:
            print(f"ERROR: invalid --set entry: {entry}")
            sys.exit(1)
        path, raw = entry.split("=", 1)
        value = _coerce_scalar(raw)
        _set_nested(book, path, value)
        changes.append({"target": path, "value": value, "source": "manual_override"})

    if args.sources:
        sources_doc = _load_json(args.sources)
        for source in sources_doc.get("sources", []):
            changes.append(_apply_source(book, source))

    book["updated_at"] = _now_iso()
    _save_json(args.price_book, book)

    print("Price book updated:")
    print(f"  File: {args.price_book}")
    for change in changes:
        print(f"  - {change['target']} = {change['value']} ({change['source']})")
    if not changes:
        print("  - No changes requested.")


if __name__ == "__main__":
    main()

