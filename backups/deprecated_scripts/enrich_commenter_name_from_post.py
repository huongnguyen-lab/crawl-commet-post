"""
Extract commenter names by loading the Facebook post once, then mapping visible
comment text back to rows in data/processed/test.csv.

Run:
    python3 scripts/enrich_commenter_name_from_post.py --rounds 80
"""

import argparse
import asyncio
import re
from urllib.parse import parse_qs, urlparse

import enrich_commenter_name_browser as browser_tools

POST_URL = "https://www.facebook.com/408308732570405/posts/1444898224349187"


def score_comment(block_text, row):
    block_words = set(browser_tools.words(block_text))
    msg_words = browser_tools.words(row.get("message", ""))[:30]
    if not block_words or not msg_words:
        return 0
    return sum(1 for word in msg_words if word in block_words)


def row_comment_id(row):
    value = row.get("id", "")
    if "_" in value:
        return value.split("_", 1)[1]
    return value


def block_has_comment_id(block, comment_id):
    if not comment_id:
        return False
    for link in block.get("links", []):
        href = link.get("href", "")
        if comment_id in href:
            return True
        try:
            query = parse_qs(urlparse(href).query)
        except Exception:
            query = {}
        if comment_id in query.get("comment_id", []):
            return True
    return False


def guess_author_from_block(block):
    for link in block.get("links", []):
        name = browser_tools.clean_candidate_name(link.get("text", ""))
        href = (link.get("href") or "").lower()
        if not name:
            continue
        if any(skip in href for skip in ["/posts/", "comment_id=", "/ufi/", "/groups/", "/hashtag/"]):
            continue
        return name

    for line in block.get("text", "").splitlines():
        name = browser_tools.clean_candidate_name(line)
        if name:
            return name
    return ""


async def dismiss_popups(page):
    for label in ["Not Now", "OK", "Để sau", "Không phải bây giờ", "Later"]:
        try:
            await page.get_by_text(label, exact=True).click(timeout=800)
            await page.wait_for_timeout(300)
        except Exception:
            pass


async def click_comment_controls(page):
    labels = [
        "Most relevant",
        "All comments",
        "Tất cả bình luận",
        "View more comments",
        "View previous comments",
        "See more comments",
        "Xem thêm bình luận",
        "Xem các bình luận trước",
        "View more replies",
        "View all",
        "Xem thêm",
    ]
    clicked = 0
    for label in labels:
        loc = page.get_by_text(label, exact=False)
        try:
            count = min(await loc.count(), 6)
        except Exception:
            count = 0
        for idx in range(count):
            try:
                await loc.nth(idx).click(timeout=700)
                clicked += 1
                await page.wait_for_timeout(500)
            except Exception:
                pass
    return clicked


async def collect_blocks(page):
    blocks = await page.locator("[role='article'], div").evaluate_all(
        """els => els
            .map(el => ({
                text: (el.innerText || '').trim(),
                links: Array.from(el.querySelectorAll('a'))
                    .map(a => ({
                        text: (a.innerText || a.getAttribute('aria-label') || '').trim(),
                        href: a.href || ''
                    }))
                    .filter(link => link.text)
            }))
            .filter(item => item.text && item.text.length > 20 && item.text.length < 2500)
        """
    )
    seen = set()
    unique = []
    for block in blocks:
        text = block.get("text", "")
        key = re.sub(r"\s+", " ", text)[:500]
        if key in seen:
            continue
        seen.add(key)
        unique.append(block)
    return unique


async def enrich_from_post(rounds=80):
    from playwright.async_api import async_playwright

    rows, fieldnames = browser_tools.load_rows_with_resume()
    missing_indexes = [i for i, row in enumerate(rows) if not row.get("commenter_name")]
    print(f"Already filled: {len(rows) - len(missing_indexes)}/{len(rows)}", flush=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(browser_tools.PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="vi-VN",
        )
        page = await context.new_page()
        await page.goto(POST_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        await dismiss_popups(page)

        for round_index in range(rounds):
            await dismiss_popups(page)
            clicked = await click_comment_controls(page)
            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(1200)

            blocks = await collect_blocks(page)
            newly_filled = 0
            for index in list(missing_indexes):
                row = rows[index]
                comment_id = row_comment_id(row)
                exact_blocks = [block for block in blocks if block_has_comment_id(block, comment_id)]
                if exact_blocks:
                    name = guess_author_from_block(exact_blocks[0])
                    if name:
                        row["commenter_name"] = name
                        missing_indexes.remove(index)
                        newly_filled += 1
                        continue

                best_score = 0
                best_block = None
                for block in blocks:
                    score = score_comment(block.get("text", ""), row)
                    if score > best_score:
                        best_score = score
                        best_block = block

                needed = min(8, max(3, len(browser_tools.words(row.get("message", ""))) // 3))
                if best_block and best_score >= needed:
                    name = guess_author_from_block(best_block)
                    if name:
                        row["commenter_name"] = name
                        missing_indexes.remove(index)
                        newly_filled += 1

            browser_tools.write_output(rows, fieldnames)
            filled = sum(1 for row in rows if row.get("commenter_name"))
            print(
                f"round {round_index + 1}/{rounds}: blocks={len(blocks)} "
                f"clicked={clicked} new={newly_filled} filled={filled}/{len(rows)}",
                flush=True,
            )
            if not missing_indexes:
                break

        await context.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=80)
    parser.add_argument("--profile", default="fb-browser-profile", help="Browser profile folder name under profiles/.")
    args = parser.parse_args()
    browser_tools.set_profile(args.profile)
    asyncio.run(enrich_from_post(rounds=args.rounds))


if __name__ == "__main__":
    main()
