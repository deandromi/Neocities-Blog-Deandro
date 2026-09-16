#!/usr/bin/env python3

from __future__ import annotations

import html
import subprocess
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree

import markdown


SITE_URL = "https://deandro.neocities.org/"
NOTES_URL = "https://deandro.neocities.org/notes.html"
FEED_URL = "https://deandro.neocities.org/rss.xml"

FEED_TITLE = "Deandro — Now updates"
FEED_DESCRIPTION = "Updates from Deandro's Now page."
SOURCE_FILE = "Now.md"
MAX_ITEMS = 30


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_commits_for_file(path: str) -> list[str]:
    output = run_git("log", "--format=%H", "--", path)
    if not output:
        return []
    return output.splitlines()[:MAX_ITEMS]


def commit_timestamp(sha: str) -> datetime:
    raw = run_git("show", "-s", "--format=%cI", sha)
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def commit_subject(sha: str) -> str:
    return run_git("show", "-s", "--format=%s", sha)


def file_at_commit(sha: str, path: str) -> str:
    return run_git("show", f"{sha}:{path}")


def markdown_to_html(text: str) -> str:
    return markdown.markdown(
        text,
        extensions=[
            "extra",
            "sane_lists",
        ],
        output_format="html5",
    )


def build_feed() -> Element:
    rss = Element("rss", {"version": "2.0"})
    channel = SubElement(rss, "channel")

    SubElement(channel, "title").text = FEED_TITLE
    SubElement(channel, "link").text = SITE_URL
    SubElement(channel, "description").text = FEED_DESCRIPTION
    SubElement(channel, "language").text = "en"
    SubElement(channel, "generator").text = "Deandro RSS generator"
    SubElement(channel, "atom:link", {
        "xmlns:atom": "http://www.w3.org/2005/Atom",
        "href": FEED_URL,
        "rel": "self",
        "type": "application/rss+xml",
    })

    commits = git_commits_for_file(SOURCE_FILE)

    if not commits:
        raise RuntimeError(f"No Git history found for {SOURCE_FILE}")

    latest_date = commit_timestamp(commits[0])
    SubElement(channel, "lastBuildDate").text = format_datetime(latest_date)

    for sha in commits:
        date = commit_timestamp(sha)
        subject = commit_subject(sha)
        markdown_text = file_at_commit(sha, SOURCE_FILE)
        rendered = markdown_to_html(markdown_text)

        item = SubElement(channel, "item")
        SubElement(item, "title").text = subject or "Now update"
        SubElement(item, "link").text = NOTES_URL
        SubElement(item, "guid", {"isPermaLink": "false"}).text = f"urn:deandro:now:{sha}"
        SubElement(item, "pubDate").text = format_datetime(date)

        description = SubElement(item, "description")
        description.text = rendered

    return rss


def write_feed(root: Element, output_path: str = "rss.xml") -> None:
    tree = ElementTree(root)
    ElementTree.indent(tree, space="  ")
    tree.write(
        output_path,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )


def main() -> None:
    if not Path(SOURCE_FILE).exists():
        raise SystemExit(f"Missing required file: {SOURCE_FILE}")

    feed = build_feed()
    write_feed(feed)
    print(f"Generated rss.xml for {SITE_URL}")


if __name__ == "__main__":
    main()
