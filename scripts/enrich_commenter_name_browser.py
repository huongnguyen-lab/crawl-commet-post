"""
Add commenter_name to test.csv by opening each permalink_url in a logged-in browser.

Setup:
    python3 -m pip install playwright
    python3 -m playwright install chromium

Login once:
    python3 enrich_commenter_name_browser.py --login

Run:
    python3 enrich_commenter_name_browser.py

Notes:
    - This does not use Graph API "from".
    - It uses a persistent local browser profile in ./profiles/fb-browser-profile.
    - Facebook's HTML changes often, so extraction is heuristic.
"""

import argparse
import asyncio
import csv
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
INPUT_CSV = ROOT / "data" / "processed" / "test.csv"
OUTPUT_CSV = ROOT / "data" / "processed" / "test_with_commenter_name.csv"
DEFAULT_PROFILE_NAME = "fb-browser-profile"
PROFILE_DIR = ROOT / "profiles" / DEFAULT_PROFILE_NAME
BADGE_OR_ACTION_LINES = {
    "top fan",
    "author",
    "tác giả",
    "thích",
    "phản hồi",
    "reply",
    "like",
    "share",
    "chia sẻ",
    "edited",
    "đã chỉnh sửa",
}


class ContentUnavailableError(Exception):
    pass


def one_line(value):
    return (value or "").replace("\\n", " ").replace("\r", " ").replace("\n", " ").strip()


def words(value):
    return re.findall(r"[\wÀ-ỹ]+", one_line(value).lower())


def comment_id_from_url(url):
    parsed = urlparse(url)
    query_id = parse_qs(parsed.query).get("comment_id", [""])[0]
    return query_id


def mobile_url(url):
    return url.replace("https://www.facebook.com/", "https://m.facebook.com/")


def score_article(article_text, message):
    article_words = set(words(article_text))
    message_words = words(message)[:30]
    if not article_words or not message_words:
        return 0
    return sum(1 for word in message_words if word in article_words)


def clean_candidate_name(value):
    text = one_line(value)
    lowered = text.lower()
    if not text or lowered in BADGE_OR_ACTION_LINES:
        return ""
    if "facebook" in lowered or "meta" == lowered:
        return ""
    if len(text) > 80:
        return ""
    return text


def is_valid_name(value):
    return bool(clean_candidate_name(value))


def load_rows_with_resume():
    with INPUT_CSV.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    if "commenter_name" not in fieldnames:
        fieldnames.append("commenter_name")

    if OUTPUT_CSV.exists():
        with OUTPUT_CSV.open(encoding="utf-8-sig", newline="") as f:
            existing_reader = csv.DictReader(f)
            existing_rows = list(existing_reader)
        names_by_id = {
            row.get("id", ""): row.get("commenter_name", "")
            for row in existing_rows
            if row.get("id") and is_valid_name(row.get("commenter_name"))
        }
        for row in rows:
            if not row.get("commenter_name") and row.get("id") in names_by_id:
                row["commenter_name"] = names_by_id[row["id"]]

    return rows, fieldnames


def write_output(rows, fieldnames):
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def guess_name(article_text, message, links=None):
    for link_text in links or []:
        name = clean_candidate_name(link_text)
        if name:
            return name

    lines = [line.strip() for line in article_text.splitlines() if line.strip()]
    msg = one_line(message)
    msg_first = one_line(message)[:45].strip()

    for line in lines:
        if msg_first and msg_first in one_line(line):
            break
        name = clean_candidate_name(line)
        if name:
            return name

    if lines:
        first = clean_candidate_name(lines[0])
        if not first:
            return ""
        if msg and first.startswith(msg[:20]):
            return ""
        return first
    return ""


def set_profile(profile_name):
    global PROFILE_DIR
    safe_name = Path(profile_name).name
    PROFILE_DIR = ROOT / "profiles" / safe_name


async def login_only(wait_seconds=300):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="vi-VN",
        )
        page = await context.new_page()
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        print(f"Login Facebook in the opened browser. This window will stay open for {wait_seconds} seconds...")
        await page.wait_for_timeout(wait_seconds * 1000)
        await context.close()


async def extract_commenter_name(page, row):
    url = mobile_url(row.get("permalink_url", ""))
    message = row.get("message", "")
    target_comment_id = comment_id_from_url(url)

    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)

    page_text = ""
    try:
        page_text = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        page_text = ""
    lowered_page_text = page_text.lower()
    if (
        "this content isn't available right now" in lowered_page_text
        or "nội dung này hiện không hiển thị" in lowered_page_text
        or "content isn't available" in lowered_page_text
    ):
        raise ContentUnavailableError("Facebook says this content is not available")

    # Try to give Facebook time to jump to/highlight the permalinked comment.
    if target_comment_id:
        await page.evaluate("window.scrollBy(0, 240)")
        await page.wait_for_timeout(800)

    candidates = []
    for selector in ["[role='article']", "div", "section"]:
        try:
            elements = await page.locator(selector).evaluate_all(
                """els => els
                    .map(el => ({
                        text: (el.innerText || '').trim(),
                        links: Array.from(el.querySelectorAll('a'))
                            .map(a => (a.innerText || a.getAttribute('aria-label') || '').trim())
                            .filter(Boolean)
                    }))
                    .filter(item => item.text && item.text.length < 2000)
                """
            )
        except Exception:
            elements = []

        for item in elements:
            text = item.get("text", "")
            score = score_article(text, message)
            if score:
                candidates.append((score, text, item.get("links", [])))

        if candidates:
            break

    if not candidates:
        return ""

    candidates.sort(key=lambda item: item[0], reverse=True)
    return guess_name(candidates[0][1], message, candidates[0][2])


async def enrich(limit=None, start=0):
    from playwright.async_api import async_playwright
    from playwright._impl._errors import TargetClosedError

    rows, fieldnames = load_rows_with_resume()

    selected_indexes = range(start, len(rows))
    if limit is not None:
        selected_indexes = range(start, min(start + limit, len(rows)))

    async def open_browser(playwright):
        context = await playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="vi-VN",
        )
        page = await context.new_page()
        return context, page

    async with async_playwright() as p:
        context, page = await open_browser(p)
        consecutive_unavailable = 0
        for index in selected_indexes:
            row = rows[index]
            if row.get("commenter_name"):
                continue

            try:
                name = await extract_commenter_name(page, row)
                consecutive_unavailable = 0
            except ContentUnavailableError as exc:
                consecutive_unavailable += 1
                print(
                    f"{index + 1}/{len(rows)} unavailable: {row.get('id')} ({consecutive_unavailable} in a row)",
                    flush=True,
                )
                if consecutive_unavailable >= 3:
                    print("Stopping because Facebook returned unavailable content repeatedly.", flush=True)
                    break
                continue
            except TargetClosedError as exc:
                print(f"{index + 1}/{len(rows)} browser closed; reopening: {exc}", flush=True)
                try:
                    await context.close()
                except Exception:
                    pass
                context, page = await open_browser(p)
                continue
            except Exception as exc:
                name = ""
                print(f"{index + 1}/{len(rows)} ERROR {row.get('id')}: {exc}", flush=True)

            row["commenter_name"] = name
            print(f"{index + 1}/{len(rows)} {row.get('id')} -> {name or '[not found]'}", flush=True)

            write_output(rows, fieldnames)

            try:
                await page.wait_for_timeout(1200)
            except TargetClosedError:
                print("browser closed during delay; reopening", flush=True)
                try:
                    await context.close()
                except Exception:
                    pass
                context, page = await open_browser(p)

        try:
            await context.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="Open browser for manual Facebook login.")
    parser.add_argument("--login-wait", type=int, default=300, help="Seconds to keep login browser open.")
    parser.add_argument("--profile", default=DEFAULT_PROFILE_NAME, help="Browser profile folder name under profiles/.")
    parser.add_argument("--limit", type=int, default=None, help="Only process N rows.")
    parser.add_argument("--start", type=int, default=0, help="Start at zero-based row index.")
    args = parser.parse_args()

    set_profile(args.profile)

    if args.login:
        asyncio.run(login_only(wait_seconds=args.login_wait))
    else:
        asyncio.run(enrich(limit=args.limit, start=args.start))


if __name__ == "__main__":
    main()
