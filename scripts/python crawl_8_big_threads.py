from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import base64
import csv
import os
import re
import time
import unicodedata
from urllib.parse import parse_qs, unquote, urlparse
from datetime import datetime

POST_URL = "https://www.facebook.com/1454150383423971/posts/1444898224349187"
OUTPUT_CSV = os.environ.get("OUTPUT_CSV", "prudential_8_big_threads_ui.csv")
PROGRESS_FILE = os.environ.get("PROGRESS_FILE", "prudential_8_big_threads_seen_ids.txt")

USER_DATA_DIR = os.environ.get(
    "FB_USER_DATA_DIR",
    os.path.join(os.environ["LOCALAPPDATA"], "Google", "Chrome", "User Data")
)

THREADS = [
    ("1444898224349187_1577480480411382", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1577480480411382"),
    # ("1444898224349187_888269780967829", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=888269780967829"),
    # ("1444898224349187_980015638258577", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=980015638258577"),
    # ("1444898224349187_1003929705676056", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1003929705676056"),
    # ("1444898224349187_1307880004272217", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1307880004272217"),
    # ("1444898224349187_1545465400302500", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1545465400302500"),
    # ("1444898224349187_1762099404957440", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1762099404957440"),
    # ("1444898224349187_1636598904300600", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1636598904300600"),
]

CSV_FIELDS = [
    "Date",
    "Comment_ID",
    "Parent_ID",
    "Level",
    "Content",
    "Status",
    "Reaction_Count",
    "Reply_Count",
    "Permalink",
    "Post_URL",
]

MORE_REPLY_TEXTS = [
    "View more replies",
    "View previous replies",
    "View all replies",
    "View more comments",
    "Xem thêm phản hồi",
    "Xem các phản hồi trước",
    "Xem thêm câu trả lời",
    "Xem thêm bình luận",
]

NOISE = {
    "Like", "Reply", "Share", "Comment", "Edited", "Author",
    "Thích", "Phản hồi", "Chia sẻ", "Bình luận",
}


def init_files():
    if not os.path.exists(OUTPUT_CSV):
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()

    if not os.path.exists(PROGRESS_FILE):
        open(PROGRESS_FILE, "w", encoding="utf-8").close()


def load_seen_ids():
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return set(x.strip() for x in f if x.strip())


def mark_seen(comment_id):
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(comment_id + "\n")


def append_rows(rows):
    if not rows:
        return

    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writerows(rows)


def extract_comment_id_from_url(url):
    ids = extract_comment_ids_from_url(url)
    return ids.get("reply_comment_id") or ids.get("comment_id") or ""


def extract_comment_ids_from_url(url):
    ids = {}
    if not url:
        return ids

    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    for key in ("comment_id", "reply_comment_id"):
        for value in params.get(key, []):
            decoded_id = decode_fb_comment_id(value)
            if decoded_id:
                ids[key] = decoded_id
                break

    m = re.search(r"comment/([^/?&#]+)", url)
    if m and "comment_id" not in ids:
        decoded_id = decode_fb_comment_id(m.group(1))
        if decoded_id:
            ids["comment_id"] = decoded_id

    return ids


def decode_fb_comment_id(value):
    if not value:
        return ""

    value = unquote(value)
    if re.fullmatch(r"\d+", value) or re.fullmatch(r"\d+_\d+", value):
        return value

    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="ignore")
    except:
        return ""

    m = re.search(r"comment:(\d+_\d+|\d+)", decoded)
    if m:
        return m.group(1)

    return ""


def normalize_comment_id(raw_id, parent_prefix="1444898224349187"):
    if not raw_id:
        return ""

    if "_" in raw_id:
        return raw_id

    return f"{parent_prefix}_{raw_id}"


def comment_suffix(comment_id):
    if not comment_id:
        return ""
    return comment_id.rsplit("_", 1)[-1]


def canonical_permalink(comment_id, parent_id="", level=1):
    if not comment_id:
        return POST_URL

    if level == 2 and parent_id:
        return (
            f"{POST_URL}?comment_id={comment_suffix(parent_id)}"
            f"&reply_comment_id={comment_suffix(comment_id)}"
        )

    return f"{POST_URL}?comment_id={comment_suffix(comment_id)}"


def remove_vietnamese_marks(text):
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").replace("đ", "d").replace("Đ", "D")


def looks_like_level_1_content(content):
    text = content.lower()
    ascii_text = remove_vietnamese_marks(text)
    has_campaign_tag = "#prudential" in ascii_text
    answer_terms = r"(?:dap\s*an|đáp\s*án|\?{1,2}p\s*\?n)"
    has_answer_choice = any(
        re.search(pattern, ascii_text, re.IGNORECASE)
        for pattern in [
            rf"{answer_terms}\s*(?:dung\s*:?\s*)?[abc](?:\b|[^a-z0-9])",
            rf"(?:^|[^a-z0-9])[abc]\s*[-:]?\s*\d{{1,3}}(?:\b|[^a-z0-9])",
            rf"\d{{1,3}}\s*{answer_terms}\s*[abc](?:\b|[^a-z0-9])",
        ]
    )

    return bool(has_answer_choice and has_campaign_tag)


def clean_content(text):
    if not text:
        return ""

    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line in NOISE:
            continue
        if re.match(r"^\d+[smhdw]$", line.lower()):
            continue
        if re.match(r"^\d+\s*(phút|giờ|ngày|tuần)$", line.lower()):
            continue
        if line.lower() in ["just now", "see translation", "xem bản dịch"]:
            continue
        lines.append(line)

    return " | ".join(lines).strip()


def extract_visible_comments(page, parent_id, parent_permalink, seen_ids):
    rows = []

    try:
        items = page.evaluate("""() => {
            const ignore = new Set([
                'Like', 'Reply', 'Share', 'Comment', 'Edited', 'Author',
                'Thích', 'Phản hồi', 'Chia sẻ', 'Bình luận',
                'Top fan', 'Most relevant', 'All reactions'
            ]);

            return Array.from(document.querySelectorAll('[role="article"]'))
                .filter(root => root.querySelectorAll('[role="article"]').length === 0)
                .map(root => {
                    const links = Array.from(root.querySelectorAll('a[href]'));
                    const replyLink = links.find(l => l.href && l.href.includes('reply_comment_id='));
                    const commentLink = links.find(l => l.href && l.href.includes('comment_id='));
                    const permalink = replyLink ? replyLink.href : (commentLink ? commentLink.href : '');

                    const nodes = Array.from(root.querySelectorAll('div[dir="auto"], span[dir="auto"]'));
                    const parts = [];
                    for (const node of nodes) {
                        const anc = node.closest('[role="button"], a[role="link"], [aria-label]');
                        if (anc) {
                            const lbl = anc.getAttribute('aria-label') || '';
                            if (/Like|Reply|Share|reaction|Thích|Phản hồi|Chia sẻ/i.test(lbl)) continue;
                        }
                        const childText = Array.from(node.children)
                            .map(c => (c.innerText || c.textContent || ''))
                            .join('\\n').trim();
                        const ownText = (node.innerText || node.textContent || '').trim();
                        if (!ownText || ownText === childText) continue;
                        if (ignore.has(ownText)) continue;
                        if (/^\\d+[smhdw]$/i.test(ownText)) continue;
                        if (/^\\d+\\s*(phút|giờ|ngày|tuần)$/i.test(ownText)) continue;
                        if (/^View (all|more)|^Xem thêm|^Xem các/i.test(ownText)) continue;
                        parts.push(ownText);
                    }

                    const linkTexts = links.map(l => (l.innerText || l.textContent || '').trim()).filter(Boolean);
                    const uniqueParts = [...new Set(parts)];
                    if (uniqueParts[0] && linkTexts.includes(uniqueParts[0])) uniqueParts.shift();

                    return { permalink, text: uniqueParts.join(' | ') };
                })
                .filter(c => c.text && c.text.length >= 2);
        }""")
    except Exception:
        return rows

    for item in items:
        try:
            content = clean_content(item.get("text", ""))
            if not content or len(content) < 2:
                continue
            if len(content) > 1200 and ("All reactions" in content or "comments" in content):
                continue

            permalink = item.get("permalink", "")
            permalink_ids = extract_comment_ids_from_url(permalink)
            raw_reply_id = permalink_ids.get("reply_comment_id", "")
            raw_comment_id = raw_reply_id or permalink_ids.get("comment_id", "")

            if raw_comment_id:
                comment_id = normalize_comment_id(raw_comment_id)
            else:
                comment_id = f"ui_fallback_{parent_id}_{abs(hash(content[:250]))}"

            if comment_id in seen_ids:
                continue

            # Bỏ qua Level 1: comment cha hoặc bài dự thi L1 lọt vào thread view
            if comment_id == parent_id or (not raw_reply_id and looks_like_level_1_content(content)):
                seen_ids.add(comment_id)
                mark_seen(comment_id)
                continue

            rows.append({
                "Date": datetime.now().isoformat(timespec="seconds"),
                "Comment_ID": comment_id,
                "Parent_ID": parent_id,
                "Level": 2,
                "Content": content,
                "Status": "OK_UI",
                "Reaction_Count": "",
                "Reply_Count": "",
                "Permalink": canonical_permalink(comment_id, parent_id, 2) or parent_permalink,
                "Post_URL": POST_URL,
            })

            seen_ids.add(comment_id)
            mark_seen(comment_id)

        except Exception:
            continue

    return rows


def switch_to_all_comments(page):
    try:
        for sel in ["text=Most relevant", "text=Newest", "text=Oldest",
                    "text=Phù hợp nhất", "text=Mới nhất", "text=Cũ nhất"]:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=800):
                btn.click()
                time.sleep(1)
                break
        options = page.locator('[role="menuitem"]').all()
        if len(options) >= 3:
            options[2].click()
            time.sleep(2)
    except:
        pass


def click_more_replies(page):
    clicked = 0
    while True:
        found = False
        for text in MORE_REPLY_TEXTS:
            try:
                btn = page.get_by_text(text, exact=False).first
                if btn.is_visible(timeout=300):
                    btn.scroll_into_view_if_needed(timeout=800)
                    btn.click(timeout=1500)
                    clicked += 1
                    time.sleep(0.8)
                    for _ in range(2):
                        page.keyboard.press("End")
                        time.sleep(0.3)
                    found = True
                    break
            except:
                continue
        if not found:
            break
    return clicked


def scroll_thread(page):
    for _ in range(3):
        page.keyboard.press("End")
        time.sleep(0.5)


def crawl_thread(page, parent_id, permalink, seen_ids, max_idle_rounds=25):
    print(f"\n==============================")
    print(f"THREAD: {parent_id}")
    print(f"URL: {permalink}")
    print(f"==============================")

    page.goto(permalink, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    switch_to_all_comments(page)

    total_new = 0
    idle_rounds = 0
    round_no = 0

    while idle_rounds < max_idle_rounds:
        round_no += 1

        clicked = click_more_replies(page)
        scroll_thread(page)

        rows = extract_visible_comments(page, parent_id, permalink, seen_ids)
        append_rows(rows)

        new_count = len(rows)
        total_new += new_count

        print(
            f"[{parent_id}] round={round_no} "
            f"clicked={clicked} new={new_count} total_new={total_new} idle={idle_rounds}"
        )

        if new_count == 0 and clicked == 0:
            idle_rounds += 1
        else:
            idle_rounds = 0

        time.sleep(1.5)

    print(f"Done thread {parent_id}: +{total_new} rows")


def main():
    init_files()
    seen_ids = load_seen_ids()

    with sync_playwright() as p:
        for parent_id, permalink in THREADS:
            context = None
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=USER_DATA_DIR,
                    headless=False,
                    viewport={"width": 1440, "height": 1200},
                    args=[
                        "--disable-notifications",
                        "--start-maximized",
                    ],
                )
                page = context.new_page()
                page.set_default_timeout(5000)

                crawl_thread(page, parent_id, permalink, seen_ids)
            except Exception as e:
                print(f"ERROR THREAD {parent_id}: {e}")

                append_rows([{
                    "Date": datetime.now().isoformat(timespec="seconds"),
                    "Comment_ID": parent_id,
                    "Parent_ID": "",
                    "Level": 1,
                    "Content": "",
                    "Status": f"ERROR_UI: {e}",
                    "Reaction_Count": "",
                    "Reply_Count": "",
                    "Permalink": permalink,
                    "Post_URL": POST_URL,
                }])
            finally:
                if context:
                    try:
                        context.close()
                    except:
                        pass

    print(f"\nSaved CSV: {OUTPUT_CSV}")
    print(f"Progress file: {PROGRESS_FILE}")


if __name__ == "__main__":
    main()
