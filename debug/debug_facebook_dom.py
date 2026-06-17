import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

POST_URL = "https://www.facebook.com/408308732570405/posts/1444898224349187"
ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "profiles" / "fb-browser-profile"


async def dismiss(page):
    for label in ["Not Now", "OK", "Để sau", "Không phải bây giờ"]:
        try:
            await page.get_by_text(label, exact=True).click(timeout=700)
            await page.wait_for_timeout(300)
        except Exception:
            pass


async def click_more(page):
    labels = [
        "Most relevant",
        "All comments",
        "Tất cả bình luận",
        "View more comments",
        "View previous comments",
        "See more comments",
        "Xem thêm bình luận",
        "Xem các bình luận trước",
        "View all",
        "Xem thêm",
    ]
    for label in labels:
        loc = page.get_by_text(label, exact=False)
        try:
            count = min(await loc.count(), 5)
        except Exception:
            count = 0
        for idx in range(count):
            try:
                await loc.nth(idx).click(timeout=600)
                await page.wait_for_timeout(300)
            except Exception:
                pass


async def main():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            locale="vi-VN",
        )
        page = await context.new_page()
        await page.goto(POST_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        await dismiss(page)
        for _ in range(4):
            await click_more(page)
            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(1000)

        needle = "Minh Anh Khánh Hà"
        data = await page.evaluate(
            """
            needle => {
              const els = [...document.querySelectorAll('div, span, a')]
                .filter(el => {
                  const text = el.innerText || '';
                  return text.includes(needle) && text.length < 700;
                });
              function info(node) {
                if (!node) return null;
                return {
                  tag: node.tagName,
                  role: node.getAttribute('role'),
                  aria: node.getAttribute('aria-label'),
                  className: String(node.className || ''),
                  text: (node.innerText || '').slice(0, 1000),
                  links: [...node.querySelectorAll('a')].slice(0, 12).map(a => ({
                    text: (a.innerText || a.getAttribute('aria-label') || '').trim(),
                    href: a.href
                  }))
                };
              }
              return els.slice(0, 12).map((el, idx) => ({
                idx,
                self: info(el),
                parent: info(el.parentElement),
                gp: info(el.parentElement && el.parentElement.parentElement),
                ggp: info(el.parentElement && el.parentElement.parentElement && el.parentElement.parentElement.parentElement),
                g4: info(el.parentElement && el.parentElement.parentElement && el.parentElement.parentElement.parentElement && el.parentElement.parentElement.parentElement.parentElement)
              }));
            }
            """,
            needle,
        )
        print(json.dumps(data, ensure_ascii=False, indent=2))
        await context.close()


asyncio.run(main())
