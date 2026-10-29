#!/usr/bin/env python3
"""Build the public Pinterest snapshot used by the scrapbook.

Pinterest's profile RSS exposes the latest 25 pins with their image and pin
URL, but no more. This keeps a rolling archive in data/pinterest.json: merge
what the feed returns into the stored set, sort by date, keep the newest 50.
Run it daily and the scrapbook fills to fifty as new pins are added.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

USER = os.environ.get("PINTEREST_USER", "Deandro2011").strip() or "Deandro2011"
FEED = f"https://www.pinterest.com/{USER}/feed.rss"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
KEEP = 50
USER_AGENT = "deandro-neocities-pinterest-archive/1.0 (+https://deandro.neocities.org/)"


def fetch_feed() -> bytes:
    request = Request(
        FEED,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml",
        },
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as error:
            last_error = error
            if isinstance(error, HTTPError) and error.code not in {429, 500, 502, 503, 504}:
                break
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Pinterest feed failed: {last_error}")


def image_from(description: str) -> str:
    match = re.search(r'<img[^>]+src="([^"]+)"', description)
    if not match:
        return ""
    return (
        match.group(1)
        .replace("/736x/", "/474x/")
        .replace("/236x/", "/474x/")
    )


def load_existing() -> dict:
    path = DATA_DIR / "pinterest.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def build_snapshot() -> dict:
    existing = load_existing()
    pins = {
        pin["id"]: pin
        for pin in existing.get("pins", [])
        if isinstance(pin, dict) and pin.get("id")
    }

    root = ElementTree.fromstring(fetch_feed())
    added = 0

    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        match = re.search(r"/pin/(\d+)", link)
        if not match:
            continue

        identifier = match.group(1)
        image = image_from(item.findtext("description") or "")
        if not image:
            continue

        try:
            stamp = parsedate_to_datetime(item.findtext("pubDate") or "")
            date = stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError):
            date = ""

        if identifier not in pins:
            added += 1
        pins[identifier] = {
            "id": identifier,
            "url": link,
            "image": image,
            "date": date,
        }

    ordered = sorted(
        pins.values(),
        key=lambda pin: pin.get("date") or "",
        reverse=True,
    )[:KEEP]

    print(f"Pinterest: {added} new, {len(ordered)} kept")
    return {
        "status": "ready" if ordered else "empty",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "username": USER,
        "profileUrl": f"https://www.pinterest.com/{USER}/",
        "pins": ordered,
    }


def write_snapshot(snapshot: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    (DATA_DIR / "pinterest.json").write_text(json_text, encoding="utf-8")
    (DATA_DIR / "pinterest.js").write_text(
        "window.DEANDRO_PINTEREST_DATA = " + json_text.rstrip() + ";\n",
        encoding="utf-8",
    )
    print(f"Pinterest snapshot ready: {len(snapshot['pins'])} pins.")


if __name__ == "__main__":
    write_snapshot(build_snapshot())
