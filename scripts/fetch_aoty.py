#!/usr/bin/env python3
"""Build the public AOTY review snapshot used by the Music room.

AOTY is behind Cloudflare, so the page is fetched through the Wayback
Machine's Save Page Now. The two review pages are captured, parsed and merged
into data/aoty.json; the newest reviews win. data/aoty.js is the window global
the site loads.
"""

from __future__ import annotations

import gzip
import html
import json
import os
import re
import time
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

USER = os.environ.get("AOTY_USER", "deandro").strip() or "deandro"
BASE = f"https://www.albumoftheyear.org/user/{USER}/reviews/"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PAGES = 2
USER_AGENT = "deandro-neocities-aoty-archive/1.0 (+https://deandro.neocities.org/)"


def fetch(url: str) -> tuple[str, str]:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "identity",
        },
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urlopen(request, timeout=90) as response:
                payload = response.read()
                encoding = (response.headers.get("Content-Encoding") or "").lower()
                if "gzip" in encoding or payload[:2] == b"\x1f\x8b":
                    payload = gzip.decompress(payload)
                elif "deflate" in encoding:
                    payload = zlib.decompress(payload)
                return response.geturl(), payload.decode("utf-8", "replace")
        except HTTPError as error:
            last_error = error
            if error.code not in {429, 500, 502, 503, 504}:
                break
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
        except (URLError, TimeoutError) as error:
            last_error = error
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"request failed for {url}: {last_error}")


def latest_capture(url: str) -> str:
    cdx = (
        "https://web.archive.org/cdx/search/cdx?output=json"
        "&filter=statuscode:200&limit=-1&url=" + quote(url, safe="")
    )
    try:
        _, body = fetch(cdx)
        rows = json.loads(body)
        if len(rows) > 1:
            return rows[-1][1]
    except (RuntimeError, ValueError):
        pass
    return ""


def age_seconds(stamp: str) -> float:
    moment = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds()


def ensure_fresh(url: str, stamp: str) -> str:
    if age_seconds(stamp) < 4 * 60 * 60:
        return stamp
    for _ in range(6):
        time.sleep(30)
        newer = latest_capture(url)
        if newer and newer > stamp:
            return newer
    print(f"archive produced no newer capture for {url}; using {stamp}")
    return stamp


def capture(url: str) -> tuple[str, str]:
    final_url, body = fetch("https://web.archive.org/save/" + url)
    match = re.search(r"/web/(\d{14})/", final_url) or re.search(r"/web/(\d{14})/", body)
    stamp = match.group(1) if match else latest_capture(url)
    if not stamp:
        raise RuntimeError(f"no Wayback capture for {url}")

    stamp = ensure_fresh(url, stamp)
    _, raw = fetch(f"https://web.archive.org/web/{stamp}id_/{url}")
    return stamp, raw


def clean(fragment: str) -> str:
    return re.sub(
        r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", fragment))
    ).strip()


def paragraphs(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"</p>", "\n\n", fragment, flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]*>", "", fragment))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return re.sub(r"\s*read more\s*$", "", text, flags=re.IGNORECASE).strip()


def first(pattern: str, block: str, flags: int = re.DOTALL) -> str:
    match = re.search(pattern, block, flags)
    return match.group(1) if match else ""


def absolute(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.albumoftheyear.org" + href
    return href


def parse_reviews(page: str) -> list[dict]:
    reviews = []
    blocks = re.split(r'<div class="albumReviewRow', page)[1:]

    for raw in blocks:
        block = re.split(r'<div class="albumReviewRow', raw)[0]
        identifier = first(r'id="review_(\d+)"', block)
        if not identifier:
            continue

        artist = clean(first(r'class="artistTitle"[^>]*>(.*?)</div>', block))
        album = clean(first(r'class="albumTitle"[^>]*>(.*?)</div>', block))
        if not artist or not album:
            continue

        score = first(r'class="rating">(\d+)</div>', block, 0)
        body = paragraphs(first(r'class="albumReviewText[^"]*">(.*?)</div>', block))
        cover = first(r'userReviewImage.*?<img[^>]*src="([^"]+)"', block)
        link = (
            first(r'class="userReviewImage.*?<a href="([^"]+)"', block)
            or first(r'href="(/user/[^"]+/album/[^"]+)"', block)
            or first(r'class="albumTitle"[^>]*>\s*<a href="([^"]+)"', block)
        )

        date = ""
        stamp = first(r'title="(\d{1,2} [A-Za-z]+ \d{4}[^"]*GMT)"', block)
        if stamp:
            try:
                date = parsedate_to_datetime(stamp).astimezone(timezone.utc).isoformat()
                date = date.replace("+00:00", "Z")
            except (TypeError, ValueError, OverflowError):
                date = ""

        reviews.append(
            {
                "id": int(identifier),
                "artist": artist,
                "album": album,
                "score": int(score or 0),
                "date": date,
                "url": absolute(link),
                "cover": absolute(cover),
                "body": body,
            }
        )

    return reviews


def load_existing() -> dict:
    path = DATA_DIR / "aoty.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def write_snapshot(snapshot: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    (DATA_DIR / "aoty.json").write_text(json_text, encoding="utf-8")
    (DATA_DIR / "aoty.js").write_text(
        "window.DEANDRO_AOTY_DATA = " + json_text.rstrip() + ";\n",
        encoding="utf-8",
    )


def main() -> None:
    existing = load_existing()
    by_id = {
        review["id"]: review
        for review in existing.get("reviews", [])
        if isinstance(review, dict) and review.get("id")
    }
    latest_stamp = existing.get("capture", "")

    for page in range(1, PAGES + 1):
        url = BASE if page == 1 else f"{BASE}{page}/"
        try:
            stamp, body = capture(url)
        except RuntimeError as error:
            print(f"page {page}: {error}")
            continue
        if stamp > latest_stamp:
            latest_stamp = stamp
        found = parse_reviews(body)
        print(f"page {page}: {len(found)} reviews (capture {stamp})")
        for review in found:
            by_id[review["id"]] = review

    reviews = sorted(
        by_id.values(),
        key=lambda review: (review.get("date") or "", review.get("id") or 0),
        reverse=True,
    )

    if not reviews:
        raise SystemExit("no reviews found; leaving the stored snapshot alone")

    snapshot = {
        "status": "ready",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "username": USER,
        "profileUrl": f"https://www.albumoftheyear.org/user/{USER}/",
        "reviewsUrl": BASE,
        "source": "wayback",
        "capture": latest_stamp,
        "reviews": reviews,
    }
    write_snapshot(snapshot)
    print(f"AOTY snapshot ready: {len(reviews)} reviews.")


if __name__ == "__main__":
    main()
