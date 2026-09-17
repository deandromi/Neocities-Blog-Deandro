#!/usr/bin/env python3
"""Build the public Chess.com snapshot used by chess.html.

Chess.com ratings and archived games are public, so no API secret is needed.
Pinned games are selected in data/chess-favourites.json.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


API_ROOT = "https://api.chess.com/pub/player"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
USERNAME = os.environ.get("CHESS_COM_USER", "deandro_m").strip() or "deandro_m"
USER_AGENT = "deandro-neocities-chess-archive/1.0 (+https://deandro.neocities.org/)"
RATING_TYPES = ("rapid", "blitz", "bullet", "daily")
DRAW_RESULTS = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}


def api_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            time.sleep(0.35)
            return payload
        except HTTPError as error:
            last_error = error
            if error.code not in {429, 500, 502, 503, 504} or attempt == 3:
                break
            retry_after = error.headers.get("Retry-After") if error.headers else None
            time.sleep(float(retry_after or (2 * (attempt + 1))))
        except (URLError, TimeoutError) as error:
            last_error = error
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Chess.com request failed for {url}: {last_error}")


def as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def pgn_headers(pgn: Any) -> dict[str, str]:
    if not isinstance(pgn, str):
        return {}
    return {
        key: value.replace(r'\"', '"')
        for key, value in re.findall(r'^\[([A-Za-z0-9_]+)\s+"((?:\\.|[^"])*)"\]\s*$', pgn, re.MULTILINE)
    }


def game_id(url: Any) -> str:
    path = urlparse(str(url or "")).path.rstrip("/")
    match = re.search(r"/(\d+)$", path)
    return match.group(1) if match else ""


def opening_name(game: dict[str, Any], headers: dict[str, str]) -> str:
    source = str(game.get("eco") or headers.get("ECOUrl") or "")
    if "/openings/" in source:
        slug = unquote(source.split("/openings/", 1)[1].strip("/"))
        return slug.replace("-", " ")
    return headers.get("Opening") or ""


def iso_from_unix(value: Any) -> str | None:
    seconds = as_int(value)
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def result_for(player_result: str, opponent_result: str) -> str:
    if player_result == "win":
        return "win"
    if player_result in DRAW_RESULTS or opponent_result in DRAW_RESULTS:
        return "draw"
    return "loss"


def parse_game(game: dict[str, Any]) -> dict[str, Any] | None:
    white = game.get("white") if isinstance(game.get("white"), dict) else {}
    black = game.get("black") if isinstance(game.get("black"), dict) else {}
    username_lower = USERNAME.lower()
    if str(white.get("username") or "").lower() == username_lower:
        player, opponent, color = white, black, "white"
    elif str(black.get("username") or "").lower() == username_lower:
        player, opponent, color = black, white, "black"
    else:
        return None

    headers = pgn_headers(game.get("pgn"))
    accuracies = game.get("accuracies") if isinstance(game.get("accuracies"), dict) else {}
    accuracy = accuracies.get(color)
    try:
        accuracy = round(float(accuracy), 1) if accuracy is not None else None
    except (TypeError, ValueError):
        accuracy = None

    player_result = str(player.get("result") or "")
    opponent_result = str(opponent.get("result") or "")
    url = str(game.get("url") or "")
    return {
        "id": game_id(url),
        "url": url,
        "date": iso_from_unix(game.get("end_time")),
        "player": str(player.get("username") or USERNAME),
        "opponent": str(opponent.get("username") or "unknown opponent"),
        "color": color,
        "rating": as_int(player.get("rating")),
        "opponentRating": as_int(opponent.get("rating")),
        "result": result_for(player_result, opponent_result),
        "termination": opponent_result if player_result == "win" else player_result,
        "timeClass": str(game.get("time_class") or "chess"),
        "timeControl": str(game.get("time_control") or ""),
        "opening": opening_name(game, headers),
        "accuracy": accuracy,
        "fen": str(game.get("fen") or ""),
    }


def parse_ratings(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ratings: dict[str, dict[str, Any]] = {}
    for name in RATING_TYPES:
        stats = payload.get(f"chess_{name}")
        stats = stats if isinstance(stats, dict) else {}
        current = stats.get("last") if isinstance(stats.get("last"), dict) else {}
        best = stats.get("best") if isinstance(stats.get("best"), dict) else {}
        record = stats.get("record") if isinstance(stats.get("record"), dict) else {}
        ratings[name] = {
            "current": as_int(current.get("rating")),
            "best": as_int(best.get("rating")),
            "wins": as_int(record.get("win")) or 0,
            "losses": as_int(record.get("loss")) or 0,
            "draws": as_int(record.get("draw")) or 0,
        }
    return ratings


def favourite_ids() -> list[str]:
    path = DATA_DIR / "chess-favourites.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    games = payload.get("games") if isinstance(payload, dict) else []
    if not isinstance(games, list):
        raise ValueError("data/chess-favourites.json must contain a games array")
    return [identifier for identifier in (game_id(url) for url in games) if identifier]


def fetch_archived_games() -> list[dict[str, Any]]:
    archives_payload = api_json(f"{API_ROOT}/{USERNAME.lower()}/games/archives")
    archive_urls = archives_payload.get("archives")
    archive_urls = archive_urls if isinstance(archive_urls, list) else []
    games: list[dict[str, Any]] = []
    for archive_url in archive_urls[-24:]:
        payload = api_json(str(archive_url))
        month_games = payload.get("games")
        if isinstance(month_games, list):
            games.extend(game for game in month_games if isinstance(game, dict))
    return games


def build_snapshot() -> dict[str, Any]:
    profile = api_json(f"{API_ROOT}/{USERNAME.lower()}")
    stats = api_json(f"{API_ROOT}/{USERNAME.lower()}/stats")
    raw_games = fetch_archived_games()
    parsed_games = [parsed for game in raw_games if (parsed := parse_game(game)) is not None]
    parsed_games.sort(key=lambda game: str(game.get("date") or ""), reverse=True)

    favourite_order = favourite_ids()
    games_by_id = {game["id"]: game for game in parsed_games if game.get("id")}
    featured = [games_by_id[identifier] for identifier in favourite_order if identifier in games_by_id]
    missing = [identifier for identifier in favourite_order if identifier not in games_by_id]
    if missing:
        print("Configured Chess.com games not found in the public archive:", ", ".join(missing))

    return {
        "status": "ready",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "username": str(profile.get("username") or USERNAME),
        "profileUrl": str(profile.get("url") or f"https://www.chess.com/member/{USERNAME}"),
        "ratings": parse_ratings(stats),
        "recentGames": parsed_games[:4],
        "featuredGames": featured,
    }


def write_snapshot(snapshot: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    (DATA_DIR / "chess.json").write_text(json_text, encoding="utf-8")
    (DATA_DIR / "chess.js").write_text(
        "window.DEANDRO_CHESS_DATA = " + json_text.rstrip() + ";\n",
        encoding="utf-8",
    )
    print(
        f"Chess snapshot ready: {len(snapshot['recentGames'])} recent games, "
        f"{len(snapshot['featuredGames'])} selected games."
    )


if __name__ == "__main__":
    write_snapshot(build_snapshot())
