"""
Facebook L1 Comment Crawler
- Chỉ lấy comment gốc (L1), không lấy replies
- Output: CSV
- Có checkpoint tự động (resume nếu bị ngắt)
- Free hoàn toàn qua Graph API

Cài đặt:
    pip install requests tqdm

Chạy:
    python fb_l1_crawler.py
"""

import requests
import csv
import json
import os
import time
from pathlib import Path
from datetime import datetime
from tqdm import tqdm

# ─── CẤU HÌNH ────────────────────────────────────────────────────────────────

PAGE_ACCESS_TOKEN = "EAAbGPDPSZAm8BRlI8MNMpg4wvOqmjZBqrKhPGnat75ZCXCCamzCiMYzfFK1v7TZCD4Dq4lZBAUQjZBlCXnDQmKJgVqMW8ZBE5tNBjRZCa8CEQmk6gjgQkIyvcQHRW3ZA1R3UdPGej1NdpIY4Qomkap2B5pjQpZAlZBz6FFKZCL6UEAWsUwcwWPU8WgtZCiZAzxKo4Lam8KnVdYJceen6GZCZBKNRHrw7HamiqeuZAw6yrj3yEBisNvbuOe1SuISO2DhJNcj4ZD"

# Danh sách post cần crawl — format: "pageID_postID"
# Ví dụ: ["123456789_987654321"]
POST_IDS = [
    "408308732570405_1444898224349187",
]

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_CSV = ROOT / "data" / "processed" / "fb_comments_L1.csv"
CHECKPOINT_FILE = ROOT / "backups" / "checkpoint.json"

# Số comment mỗi request (tối đa 100)
LIMIT = 100

# Delay giữa các request (giây) — tránh rate limit
REQUEST_DELAY = 0.5

# ─── HÀM CHÍNH ────────────────────────────────────────────────────────────────

def load_checkpoint():
    """Load checkpoint nếu có — để resume khi bị ngắt."""
    if CHECKPOINT_FILE.exists():
        with CHECKPOINT_FILE.open("r") as f:
            return json.load(f)
    return {}

def save_checkpoint(data):
    with CHECKPOINT_FILE.open("w") as f:
        json.dump(data, f, indent=2)

def post_url_from_id(post_id: str) -> str:
    """Tạo URL post từ post_id dạng pageID_postID."""
    parts = post_id.split("_")
    if len(parts) == 2:
        return f"https://www.facebook.com/{parts[0]}/posts/{parts[1]}"
    return f"https://www.facebook.com/permalink/{post_id}"

def fetch_total_count(post_id: str) -> int:
    """Lấy tổng số L1 comments (kể cả đã xóa) qua summary=true."""
    url = f"https://graph.facebook.com/v19.0/{post_id}/comments"
    params = {
        "access_token": PAGE_ACCESS_TOKEN,
        "filter": "toplevel",
        "summary": "true",
        "limit": 0,
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
        return data.get("summary", {}).get("total_count", 0)
    except Exception:
        return 0

def fetch_comments_page(post_id: str, after_cursor: str = None):
    """
    Gọi Graph API lấy 1 trang comments.
    Trả về (list_comments, next_cursor hoặc None)
    """
    url = f"https://graph.facebook.com/v19.0/{post_id}/comments"
    params = {
        "access_token": PAGE_ACCESS_TOKEN,
        "fields": "id,message,created_time,like_count,comment_count,from",
        "limit": LIMIT,
        "filter": "toplevel",   # ← chỉ lấy L1, bỏ qua replies
        "summary": "false",
    }
    if after_cursor:
        params["after"] = after_cursor

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        print(f"\n[ERROR] Request failed: {e}")
        return [], None
    except json.JSONDecodeError:
        print(f"\n[ERROR] Invalid JSON response")
        return [], None

    if "error" in data:
        err = data["error"]
        print(f"\n[API ERROR] {err.get('code')} — {err.get('message')}")
        # Rate limit → chờ 5 phút rồi retry
        if err.get("code") in [4, 17, 32, 613]:
            print("[RATE LIMIT] Chờ 5 phút...")
            time.sleep(300)
        return [], None

    comments = data.get("data", [])
    next_cursor = None
    paging = data.get("paging", {})
    if "next" in paging:
        next_cursor = paging.get("cursors", {}).get("after")

    return comments, next_cursor

def crawl_post(post_id: str, checkpoint: dict, writer, seen_ids: set):
    """Crawl toàn bộ L1 comments của 1 post."""
    post_url = post_url_from_id(post_id)
    after_cursor = checkpoint.get(post_id, {}).get("after_cursor")
    page_num = checkpoint.get(post_id, {}).get("page", 0)
    total = checkpoint.get(post_id, {}).get("total", 0)

    # Lấy tổng count thực (kể cả deleted) để tracking
    total_count = fetch_total_count(post_id)

    print(f"\n📄 Post: {post_url}")
    print(f"   📊 Tổng L1 theo API: {total_count} (kể cả đã xóa)")
    if after_cursor:
        print(f"   ↪ Resume từ trang {page_num + 1} ({total} comments đã lấy)")

    pbar = tqdm(initial=total, total=total_count, unit="cmt", desc="  L1 comments")

    deleted_count = 0

    while True:
        comments, next_cursor = fetch_comments_page(post_id, after_cursor)
        time.sleep(REQUEST_DELAY)

        for cmt in comments:
            cmt_id = cmt.get("id", "")
            if cmt_id in seen_ids:
                continue
            seen_ids.add(cmt_id)

            # Parse timestamp
            raw_time = cmt.get("created_time", "")
            try:
                dt = datetime.strptime(raw_time, "%Y-%m-%dT%H:%M:%S+0000")
                date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                date_str = raw_time

            message = cmt.get("message", "")
            is_deleted = message == ""

            if is_deleted:
                deleted_count += 1

            writer.writerow({
                "Date": date_str,
                "Comment_ID": cmt_id,
                "Author_ID": cmt.get("from", {}).get("id", ""),
                "Author_Name": cmt.get("from", {}).get("name", ""),
                "Content": "[deleted]" if is_deleted else message.replace("\n", " "),
                "Status": "deleted" if is_deleted else "active",
                "Reaction_Count": cmt.get("like_count", 0),
                "Reply_Count": cmt.get("comment_count", 0),
                "Post_URL": post_url,
            })
            total += 1
            pbar.update(1)

        page_num += 1

        # Lưu checkpoint sau mỗi trang
        checkpoint[post_id] = {
            "after_cursor": next_cursor,
            "page": page_num,
            "total": total,
        }
        save_checkpoint(checkpoint)

        if not next_cursor:
            break
        after_cursor = next_cursor

    pbar.close()
    active = total - deleted_count
    print(f"   ✅ Xong — {total} comments L1 ({active} active, {deleted_count} deleted)")
    return total

def main():
    checkpoint = load_checkpoint()
    seen_ids = set()

    fieldnames = [
        "Date", "Comment_ID", "Author_ID", "Author_Name",
        "Content", "Status", "Reaction_Count", "Reply_Count", "Post_URL"
    ]

    # Append mode để không mất data nếu resume
    file_exists = OUTPUT_CSV.exists()
    csv_file = OUTPUT_CSV.open("a", newline="", encoding="utf-8-sig")
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    if not file_exists:
        writer.writeheader()

    grand_total = 0
    start = time.time()

    for post_id in POST_IDS:
        post_id = post_id.strip()
        if not post_id:
            continue
        n = crawl_post(post_id, checkpoint, writer, seen_ids)
        grand_total += n

    csv_file.close()

    elapsed = time.time() - start
    print(f"\n{'='*50}")
    print(f"✅ Hoàn thành: {grand_total:,} comments")
    print(f"⏱  Thời gian: {elapsed/60:.1f} phút")
    print(f"📁 File: {OUTPUT_CSV}")
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        print(f"🗑  Checkpoint đã xóa")

if __name__ == "__main__":
    main()
