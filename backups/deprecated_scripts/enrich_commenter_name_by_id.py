"""
Fill commenter_name by reading Facebook author links that carry each comment_id.

This is stricter than text matching:
    test.csv id: 1444898224349187_2870170483336721
    Facebook author link query: comment_id=base64("comment:1444898224349187_2870170483336721")

Run:
    python3 enrich_commenter_name_by_id.py --rounds 80
"""

import argparse
import asyncio
import base64
import csv
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import enrich_commenter_name_browser as browser_tools
from enrich_commenter_name_from_post import POST_URL, click_comment_controls, dismiss_popups

ROOT = Path(__file__).resolve().parents[1]
INPUT_CSV = ROOT / "data" / "processed" / "test.csv"
OUTPUT_CSV = ROOT / "data" / "processed" / "test_with_commenter_name_correct.csv"


def comment_id_from_row_id(row_id):
    return row_id.split("_", 1)[1] if "_" in row_id else row_id


def decode_fb_comment_token(token):
    token = unquote(token or "")
    if not token:
        return ""

    if token.isdigit():
        return token

    padded = token + ("=" * (-len(token) % 4))
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            decoded = decoder(padded.encode("utf-8")).decode("utf-8", errors="ignore")
        except Exception:
            continue
        match = re.search(r"comment:\d+_(\d+)", decoded)
        if match:
            return match.group(1)
    return ""


def is_plausible_author_name(text):
    name = browser_tools.clean_candidate_name(text)
    if not name:
        return ""
    lowered = name.lower()
    if re.fullmatch(r"\d+\s*(s|m|h|d|w|y)", lowered):
        return ""
    if lowered in {"like", "reply", "share", "3d", "1w", "2w", "view all"}:
        return ""
    if name.startswith("#"):
        return ""
    return name


def load_rows():
    with INPUT_CSV.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if "commenter_name" not in fields:
        fields.append("commenter_name")
    for row in rows:
        row["commenter_name"] = ""
    return rows, fields


def write_rows(rows, fields):
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


async def harvest_author_map(page):
    links = await page.locator("a").evaluate_all(
        """anchors => anchors.map(a => ({
            text: (a.innerText || a.getAttribute('aria-label') || '').trim(),
            href: a.href || ''
        })).filter(item => item.text && item.href.includes('comment_id='))"""
    )

    author_by_comment_id = {}
    for link in links:
        name = is_plausible_author_name(link.get("text", ""))
        if not name:
            continue

        href = link.get("href", "")
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        tokens = query.get("comment_id", [])
        for token in tokens:
            comment_id = decode_fb_comment_token(token)
            if not comment_id:
                continue
            # Author/profile links carry encoded comment ids. Timestamp links carry
            # plain ids with time text, already filtered above.
            author_by_comment_id.setdefault(comment_id, name)
    return author_by_comment_id


async def enrich(rounds=80):
    from playwright.async_api import async_playwright

    rows, fields = load_rows()
    needed_ids = {comment_id_from_row_id(row["id"]) for row in rows}
    author_by_id = {}

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(browser_tools.PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="vi-VN",
        )
        page = await context.new_page()
        await page.goto(POST_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        await dismiss_popups(page)

        for round_index in range(rounds):
            await dismiss_popups(page)
            clicked = await click_comment_controls(page)
            author_by_id.update(await harvest_author_map(page))

            for row in rows:
                comment_id = comment_id_from_row_id(row["id"])
                if comment_id in author_by_id:
                    row["commenter_name"] = author_by_id[comment_id]
            write_rows(rows, fields)

            filled = sum(1 for row in rows if row.get("commenter_name"))
            found_needed = len(needed_ids.intersection(author_by_id.keys()))
            print(
                f"round {round_index + 1}/{rounds}: clicked={clicked} "
                f"found_ids={found_needed}/{len(needed_ids)} filled={filled}/{len(rows)}",
                flush=True,
            )
            if filled == len(rows):
                break

            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(1200)

        await context.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=80)
    parser.add_argument("--profile", default="fb-browser-profile", help="Browser profile folder name under profiles/.")
    args = parser.parse_args()
    browser_tools.set_profile(args.profile)
    asyncio.run(enrich(rounds=args.rounds))


if __name__ == "__main__":
    main()
