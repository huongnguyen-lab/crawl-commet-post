import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "profiles" / "fb-firefox-test"
URL = "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1976222073099188"


async def main():
    async with async_playwright() as p:
        context = await p.firefox.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="vi-VN",
        )
        page = await context.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        print("Firefox opened test permalink. Login/check the page, waiting 180 seconds...", flush=True)
        await page.wait_for_timeout(180000)

        try:
            text = (await page.locator("body").inner_text(timeout=3000)).lower()
            unavailable = (
                "content isn't available" in text
                or "this content isn't available" in text
                or "nội dung này" in text
            )
            print(f"content_unavailable={unavailable}", flush=True)
            print(text[:500].replace("\n", " "), flush=True)
        except Exception as exc:
            print(f"read_error={exc}", flush=True)

        await context.close()


asyncio.run(main())
