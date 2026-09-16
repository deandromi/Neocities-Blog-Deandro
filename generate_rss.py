#!/usr/bin/env python3
from __future__ import annotations

import email.utils
import html
import re
import subprocess
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

import markdown

NOTE_FILE = "Now.md"
MAX_ITEMS = 30

SITE_URL = "https://deandrobeta.neocities.org/"
NOTES_URL = "https://deandrobeta.neocities.org/notes.html"
FEED_URL = "https://deandrobeta.neocities.org/rss.xml"

FEED_TITLE = "Deandro — Now updates"
FEED_DESCRIPTION = "Updates from Deandro's Now.md, written in Obsidian and published on Neocities."


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def plain_summary(md: str, limit: int = 280) -> str:
    text = re.sub(r"```.*?```", " ", md, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[#>*+\-\s]+", "", text, flags=re.M)
    text = re.sub(r"[*_~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def commit_rows() -> list[tuple[str, str]]:
    raw = git("log", f"--format=%H%x1f%cI", "--", NOTE_FILE)
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        sha, iso_date = line.split("\x1f", 1)
        rows.append((sha, iso_date))
    return rows[:MAX_ITEMS]


def note_at_commit(sha: str) -> str | None:
    try:
        return git("show", f"{sha}:{NOTE_FILE}")
    except subprocess.CalledProcessError:
        return None


def main() -> None:
    commits = commit_rows()
    if not commits:
        raise SystemExit(f"No Git history found for {NOTE_FILE}")

    content_ns = "http://purl.org/rss/1.0/modules/content/"
    atom_ns = "http://www.w3.org/2005/Atom"
    ET.register_namespace("content", content_ns)
    ET.register_namespace("atom", atom_ns)

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = FEED_TITLE
    ET.SubElement(channel, "link").text = SITE_URL
    ET.SubElement(channel, "description").text = FEED_DESCRIPTION
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(
        channel,
        f"{{{atom_ns}}}link",
        {"href": FEED_URL, "rel": "self", "type": "application/rss+xml"},
    )

    newest_dt = datetime.fromisoformat(commits[0][1])
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(newest_dt)

    for sha, iso_date in commits:
        md = note_at_commit(sha)
        if md is None:
            continue

        dt = datetime.fromisoformat(iso_date)
        rendered = markdown.markdown(
            md,
            extensions=["extra", "sane_lists"],
            output_format="html5",
        )

        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = f"Now update — {dt.strftime('%d %b %Y %H:%M')}"
        ET.SubElement(item, "link").text = NOTES_URL
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"urn:deandro:now:{sha}"
        ET.SubElement(item, "pubDate").text = email.utils.format_datetime(dt)
        ET.SubElement(item, "description").text = plain_summary(md)
        ET.SubElement(item, f"{{{content_ns}}}encoded").text = rendered

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write("rss.xml", encoding="utf-8", xml_declaration=True)

    print(f"Wrote rss.xml with up to {MAX_ITEMS} Now.md updates.")


if __name__ == "__main__":
    main()
