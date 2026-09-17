#!/usr/bin/env python3
"""Build the static Last.fm snapshot used by rotation.html.

Only the GitHub Action sees LASTFM_API_KEY. The generated browser files contain
public listening data, never the key itself.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


API_URL = "https://ws.audioscrobbler.com/2.0/"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
API_KEY = os.environ.get("LASTFM_API_KEY", "").strip()
USERNAME = os.environ.get("LASTFM_USER", "Deandro").strip() or "Deandro"
TIMEZONE_NAME = os.environ.get("LASTFM_TIMEZONE", "Europe/Amsterdam").strip()
PERIODS = ("7day", "1month", "3month", "6month", "12month", "overall")
TOP_LIMIT = 10


def api_call(method: str, **params: Any) -> dict[str, Any]:
    query = {
        "method": method,
        "api_key": API_KEY,
        "format": "json",
        **{key: value for key, value in params.items() if value is not None},
    }
    url = f"{API_URL}?{urlencode(query)}"
    request = Request(url, headers={"User-Agent": "deandro-neocities-listening-archive/1.0"})
    last_error: Exception | None = None

    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            if "error" in payload:
                raise RuntimeError(f"Last.fm error {payload['error']}: {payload.get('message', 'unknown error')}")
            time.sleep(0.24)
            return payload
        except (HTTPError, URLError, TimeoutError, RuntimeError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Last.fm request failed for {method}: {last_error}")


def as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def text_value(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("#text") or value.get("name") or "")
    return str(value or "")


def image_url(item: dict[str, Any]) -> str:
    images = item.get("image")
    if not isinstance(images, list):
        return ""
    for image in reversed(images):
        if isinstance(image, dict) and image.get("#text"):
            return str(image["#text"]).replace("http://", "https://", 1)
    return ""


def iso_from_unix(value: Any) -> str | None:
    seconds = as_int(value)
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_now_playing(track: dict[str, Any] | None) -> dict[str, Any] | None:
    if not track:
        return None
    attributes = track.get("@attr") if isinstance(track.get("@attr"), dict) else {}
    date_info = track.get("date") if isinstance(track.get("date"), dict) else {}
    return {
        "name": str(track.get("name") or "unknown track"),
        "artist": text_value(track.get("artist")) or "unknown artist",
        "album": text_value(track.get("album")),
        "url": str(track.get("url") or f"https://www.last.fm/user/{USERNAME}"),
        "image": image_url(track),
        "nowPlaying": attributes.get("nowplaying") == "true",
        "playedAt": iso_from_unix(date_info.get("uts")),
    }


def parse_top_items(payload: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    container_names = {
        "artists": ("topartists", "artist"),
        "albums": ("topalbums", "album"),
        "tracks": ("toptracks", "track"),
    }
    container_name, item_name = container_names[kind]
    container = payload.get(container_name, {})
    raw_items = container.get(item_name, []) if isinstance(container, dict) else []
    if isinstance(raw_items, dict):
        raw_items = [raw_items]

    parsed: list[dict[str, Any]] = []
    for item in raw_items[:TOP_LIMIT]:
        if not isinstance(item, dict):
            continue
        row = {
            "name": str(item.get("name") or "unknown"),
            "playcount": as_int(item.get("playcount")) or 0,
            "url": str(item.get("url") or ""),
            "image": image_url(item),
        }
        if kind in {"albums", "tracks"}:
            row["artist"] = text_value(item.get("artist")) or "unknown artist"
        parsed.append(row)
    return parsed


def fetch_charts() -> dict[str, dict[str, list[dict[str, Any]]]]:
    methods = {
        "artists": "user.getTopArtists",
        "albums": "user.getTopAlbums",
        "tracks": "user.getTopTracks",
    }
    charts: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for period in PERIODS:
        charts[period] = {}
        for kind, method in methods.items():
            payload = api_call(method, user=USERNAME, period=period, limit=TOP_LIMIT)
            charts[period][kind] = parse_top_items(payload, kind)
    return charts


def fetch_activity(local_timezone: ZoneInfo) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    today = datetime.now(local_timezone).date()
    first_day = today - timedelta(days=29)
    start = datetime.combine(first_day, day_time.min, tzinfo=local_timezone)
    from_timestamp = int(start.timestamp())
    counts: Counter[date] = Counter()
    first_track: dict[str, Any] | None = None
    page = 1
    total_pages = 1

    while page <= total_pages:
        payload = api_call(
            "user.getRecentTracks",
            user=USERNAME,
            limit=200,
            page=page,
            **{"from": from_timestamp},
        )
        recent = payload.get("recenttracks", {})
        tracks = recent.get("track", []) if isinstance(recent, dict) else []
        if isinstance(tracks, dict):
            tracks = [tracks]
        if page == 1 and tracks:
            first_track = tracks[0]

        for track in tracks:
            if not isinstance(track, dict):
                continue
            info = track.get("date")
            if not isinstance(info, dict) or not info.get("uts"):
                continue
            listened_at = datetime.fromtimestamp(int(info["uts"]), tz=timezone.utc).astimezone(local_timezone)
            if listened_at.date() >= first_day:
                counts[listened_at.date()] += 1

        attributes = recent.get("@attr", {}) if isinstance(recent, dict) else {}
        total_pages = min(as_int(attributes.get("totalPages")) or 1, 60)
        page += 1

    points = []
    cursor = first_day
    while cursor <= today:
        points.append(
            {
                "date": cursor.isoformat(),
                "label": cursor.strftime("%-d %B"),
                "shortLabel": cursor.strftime("%-d %b"),
                "count": counts[cursor],
            }
        )
        cursor += timedelta(days=1)
    return points, first_track


def build_snapshot() -> dict[str, Any]:
    if not API_KEY:
        raise SystemExit("LASTFM_API_KEY is required")

    try:
        local_timezone = ZoneInfo(TIMEZONE_NAME)
    except Exception as error:
        raise SystemExit(f"Unknown LASTFM_TIMEZONE {TIMEZONE_NAME!r}: {error}") from error

    info_payload = api_call("user.getInfo", user=USERNAME)
    user = info_payload.get("user", {})
    registered = user.get("registered", {}) if isinstance(user.get("registered"), dict) else {}
    activity, latest_track = fetch_activity(local_timezone)

    return {
        "status": "ready",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "username": USERNAME,
        "profileUrl": str(user.get("url") or f"https://www.last.fm/user/{USERNAME}"),
        "user": {
            "playcount": as_int(user.get("playcount")),
            "artistCount": as_int(user.get("artist_count")),
            "albumCount": as_int(user.get("album_count")),
            "trackCount": as_int(user.get("track_count")),
            "registeredAt": iso_from_unix(registered.get("unixtime")),
        },
        "nowPlaying": parse_now_playing(latest_track),
        "activity": {"days": 30, "points": activity},
        "charts": fetch_charts(),
    }


def write_snapshot(snapshot: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    (DATA_DIR / "lastfm.json").write_text(json_text, encoding="utf-8")
    (DATA_DIR / "lastfm.js").write_text(
        "window.DEANDRO_LASTFM_DATA = " + json_text.rstrip() + ";\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    write_snapshot(build_snapshot())
