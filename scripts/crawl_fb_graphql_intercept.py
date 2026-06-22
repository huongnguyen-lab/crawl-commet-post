from playwright.sync_api import sync_playwright
import os, csv, json, time, re, base64
from datetime import datetime

POST_URL = "https://www.facebook.com/1454150383423971/posts/1444898224349187"

THREADS = [
    ("1444898224349187_1577480480411382", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1577480480411382"),
    ("1444898224349187_888269780967829", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=888269780967829"),
    ("1444898224349187_980015638258577", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=980015638258577"),
    ("1444898224349187_1003929705676056", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1003929705676056"),
    ("1444898224349187_1307880004272217", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1307880004272217"),
    ("1444898224349187_1545465400302500", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1545465400302500"),
    ("1444898224349187_1762099404957440", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1762099404957440"),
    ("1444898224349187_1636598904300600", "https://www.facebook.com/1454150383423971/posts/1444898224349187?comment_id=1636598904300600"),
]

OUTPUT_CSV = "prudential_8_threads_graphql.csv"
SEEN_FILE = "seen_graphql_ids.txt"

USER_DATA_DIR = os.environ.get(
    "FB_USER_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "data", "browser-profile", "facebook_capture")
)

FIELDS = [
    "Date",
    "Comment_ID",
    "Parent_ID",
    "Level",
    "Author",
    "Content",
    "Status",
    "Permalink",
    "Post_URL",
]


def init_files():
    if not os.path.exists(OUTPUT_CSV):
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writeheader()

    if not os.path.exists(SEEN_FILE):
        open(SEEN_FILE, "w", encoding="utf-8").close()


def load_seen():
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(x.strip() for x in f if x.strip())


def save_seen(comment_id):
    with open(SEEN_FILE, "a", encoding="utf-8") as f:
        f.write(comment_id + "\n")


def append_csv(rows):
    if not rows:
        return
    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
        csv.DictWriter(f, fieldnames=FIELDS).writerows(rows)


def normalize_id(raw_id):
    if not raw_id:
        return ""
    try:
        padding = (4 - len(raw_id) % 4) % 4
        decoded = base64.b64decode(raw_id + "=" * padding).decode("utf-8")
        if decoded.startswith("comment:"):
            return decoded[len("comment:"):]
    except Exception:
        pass
    if "_" in raw_id:
        return raw_id
    return f"1444898224349187_{raw_id}"


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from walk(x)


def extract_edges_from_json(data):
    results = []

    for obj in walk(data):
        rc = obj.get("replies_connection")
        if isinstance(rc, dict) and isinstance(rc.get("edges"), list):
            for edge in rc["edges"]:
                node = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(node, dict):
                    continue

                body = node.get("body") or {}
                author = node.get("author") or {}

                content = ""
                if isinstance(body, dict):
                    content = body.get("text") or ""

                comment_id = normalize_id(node.get("id") or node.get("legacy_fbid") or "")

                if content:
                    results.append({
                        "id": comment_id,
                        "author": author.get("name", "") if isinstance(author, dict) else "",
                        "content": content,
                        "created_time": node.get("created_time", ""),
                    })

    return results


def make_handler(current_parent_ref, seen):
    def handle_response(response):
        try:
            url = response.url
            if "graphql" not in url:
                return

            text = response.text()
            if "replies_connection" not in text:
                return

            # Facebook sometimes returns newline-delimited JSON-ish chunks
            chunks = []
            raw = text.strip()

            try:
                chunks.append(json.loads(raw))
            except:
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunks.append(json.loads(line))
                    except:
                        pass

            rows = []

            for data in chunks:
                replies = extract_edges_from_json(data)

                for r in replies:
                    cid = r["id"] or f"fallback_{abs(hash(r['content'][:300]))}"

                    if cid in seen:
                        continue

                    seen.add(cid)
                    save_seen(cid)

                    ct = r.get("created_time")
                    date_str = datetime.fromtimestamp(ct).isoformat(timespec="seconds") if ct else ""

                    rows.append({
                        "Date": date_str,
                        "Comment_ID": cid,
                        "Parent_ID": current_parent_ref["parent_id"],
                        "Level": 2,
                        "Author": r.get("author", ""),
                        "Content": r.get("content", ""),
                        "Status": "OK_GRAPHQL",
                        "Permalink": "",
                        "Post_URL": POST_URL,
                    })

            if rows:
                append_csv(rows)
                print(f"   +{len(rows)} replies captured from GraphQL")

        except Exception as e:
            print(f"   response parse error: {e}")

    return handle_response


def switch_to_all_comments(page):
    try:
        # Open the sort dropdown (text= does substring match, handles BOM)
        for sel in ["text=Most relevant", "text=Newest", "text=Mới nhất",
                    "text=Phù hợp nhất", "text=All Comments", "text=Tất cả bình luận"]:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=800):
                btn.click()
                time.sleep(1.5)
                break

        # Click 3rd menuitem = "All Comments" (positional, language-independent)
        options = page.locator('[role="menuitem"]').all()
        if len(options) >= 3:
            options[2].click()
            time.sleep(2.5)
            print("   Switched to All Comments")
            return True
    except:
        pass

    print("   WARNING: could not switch to All Comments")
    return False


def click_more_replies(page):
    texts = [
        "View more replies",
        "View previous replies",
        "View all replies",
        "Xem thêm phản hồi",
        "Xem các phản hồi trước",
        "Xem thêm câu trả lời",
    ]

    clicked = 0

    for text in texts:
        try:
            btns = page.get_by_text(text, exact=False).all()
        except:
            continue

        for btn in btns:
            try:
                if not btn.is_visible(timeout=300):
                    continue

                btn.scroll_into_view_if_needed(timeout=1000)
                time.sleep(0.3)
                btn.click(timeout=2000)
                clicked += 1
                time.sleep(2)
            except:
                pass

    return clicked


def crawl_thread(page, parent_id, url):
    print("\n===================================")
    print("THREAD:", parent_id)
    print("===================================")

    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(6)

    switch_to_all_comments(page)

    idle = 0
    round_no = 0

    while idle < 40:
        round_no += 1

        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.5)

        clicked = click_more_replies(page)

        time.sleep(1.5)

        print(f"round={round_no} clicked={clicked} idle={idle}")

        if clicked == 0:
            idle += 1
        else:
            idle = 0

    print("Done:", parent_id)


def main():
    init_files()
    seen = load_seen()

    current_parent_ref = {"parent_id": ""}

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            headless=False,
            viewport={"width": 1440, "height": 1200},
            args=["--disable-notifications", "--start-maximized"],
        )

        page = context.new_page()
        page.set_default_timeout(5000)

        page.on("response", make_handler(current_parent_ref, seen))

        for parent_id, url in THREADS:
            current_parent_ref["parent_id"] = parent_id
            crawl_thread(page, parent_id, url)

        context.close()

    print("\nSaved:", OUTPUT_CSV)


if __name__ == "__main__":
    main()