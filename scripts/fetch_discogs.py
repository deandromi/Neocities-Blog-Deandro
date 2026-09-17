#!/usr/bin/env python3
"""Build the public Discogs wantlist snapshot used by rotation.html.

Only GitHub Actions sees DISCOGS_TOKEN. Generated files contain public
wantlist data and never include the token.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


API_ROOT = "https://api.discogs.com"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TOKEN = os.environ.get("DISCOGS_TOKEN", "").strip()
USERNAME = os.environ.get("DISCOGS_USER", "Deandro").strip() or "Deandro"
USER_AGENT = "deandro-neocities-wantlist/1.0 +https://deandro.neocities.org"


def api_call(path: str, **params: Any) -> dict[str, Any]:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    url = f"{API_ROOT}{path}" + (f"?{query}" if query else "")
    request = Request(
        url,
        headers={
            "Authorization": f"Discogs token={TOKEN}",
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.discogs.v2.discogs+json",
        },
    )
    last_error: Exception | None = None

    for attempt in range(4):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            time.sleep(0.35)
            return payload
        except HTTPError as error:
            last_error = error
            retry_after = error.headers.get("Retry-After") if error.headers else None
            if error.code not in {429, 500, 502, 503, 504} or attempt == 3:
                break
            time.sleep(float(retry_after or (2 * (attempt + 1))))
        except (URLError, TimeoutError) as error:
            last_error = error
            if attempt < 3:
                time.sleep(2 * (attempt + 1))

    raise RuntimeError(f"Discogs request failed for {path}: {last_error}")


def clean_url(value: Any) -> str:
    url = str(value or "")
    return "https://" + url[7:] if url.startswith("http://") else url


def artist_name(info: dict[str, Any]) -> str:
    artists = info.get("artists")
    if not isinstance(artists, list):
        return "unknown artist"
    names = [str(artist.get("name") or "").strip() for artist in artists if isinstance(artist, dict)]
    return ", ".join(name for name in names if name) or "unknown artist"


def format_details(info: dict[str, Any]) -> tuple[str, str]:
    formats = info.get("formats")
    if not isinstance(formats, list):
        return "unknown format", "other"

    parts: list[str] = []
    tokens: list[str] = []
    for item in formats:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        descriptions = item.get("descriptions")
        descriptions = descriptions if isinstance(descriptions, list) else []
        descriptions = [str(value).strip() for value in descriptions if str(value).strip()]
        quantity = str(item.get("qty") or "").strip()
        label = " · ".join([value for value in [name, *descriptions] if value])
        if quantity and quantity != "1":
            label = f"{quantity}× {label}"
        if label:
            parts.append(label)
        tokens.extend([name.lower(), *(value.lower() for value in descriptions)])

    joined = " ".join(tokens)
    if '7"' in joined or "7 inch" in joined:
        category = "7-inch"
    elif "cd" in tokens or "compact disc" in joined:
        category = "cd"
    elif "vinyl" in tokens and ("lp" in tokens or '12"' in joined or "12 inch" in joined):
        category = "lp"
    else:
        category = "other"
    return " / ".join(parts) or "unknown format", category


def parse_release(want: dict[str, Any]) -> dict[str, Any] | None:
    info = want.get("basic_information")
    if not isinstance(info, dict):
        return None
    release_id = info.get("id") or want.get("id")
    if not release_id:
        return None
    format_label, format_category = format_details(info)
    return {
        "id": int(release_id),
        "artist": artist_name(info),
        "title": str(info.get("title") or "unknown release"),
        "year": int(info.get("year") or 0) or None,
        "format": format_label,
        "formatCategory": format_category,
        "image": clean_url(info.get("cover_image") or info.get("thumb")),
        "dateAdded": str(want.get("date_added") or ""),
        "url": f"https://www.discogs.com/release/{int(release_id)}",
    }


def fetch_wantlist() -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    page = 1
    pages = 1
    user_path = quote(USERNAME, safe="")

    while page <= pages:
        payload = api_call(f"/users/{user_path}/wants", page=page, per_page=100)
        wants = payload.get("wants")
        if not isinstance(wants, list):
            wants = []
        for want in wants:
            if isinstance(want, dict):
                release = parse_release(want)
                if release:
                    releases.append(release)
        pagination = payload.get("pagination")
        pagination = pagination if isinstance(pagination, dict) else {}
        pages = min(max(int(pagination.get("pages") or 1), 1), 200)
        page += 1

    releases.sort(key=lambda release: release["dateAdded"], reverse=True)
    return releases


def build_snapshot() -> dict[str, Any]:
    if not TOKEN:
        raise SystemExit("DISCOGS_TOKEN is required")
    releases = fetch_wantlist()
    return {
        "status": "ready",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "username": USERNAME,
        "profileUrl": f"https://www.discogs.com/user/{quote(USERNAME, safe='')}",
        "wantlistUrl": f"https://www.discogs.com/wantlist?user={quote(USERNAME, safe='')}",
        "total": len(releases),
        "releases": releases,
    }


def write_snapshot(snapshot: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    (DATA_DIR / "discogs-wantlist.json").write_text(json_text, encoding="utf-8")
    (DATA_DIR / "discogs-wantlist.js").write_text(
        "window.DEANDRO_DISCOGS_DATA = " + json_text.rstrip() + ";\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    write_snapshot(build_snapshot())
