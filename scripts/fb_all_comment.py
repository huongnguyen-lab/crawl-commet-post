"""
Facebook comment crawler that walks the comment tree.

Why not only `filter=stream`?
    Facebook may report a very large summary.total_count, but the stream edge can
    stop paging early. To get more data, this script crawls top-level comments
    first, then fetches replies from each comment's /comments edge.

Output:
    data/processed/fb_all_comments.csv

Run:
    python3 scripts/fb_all_comment.py
"""

import csv
import json
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import requests
from tqdm import tqdm

PAGE_ACCESS_TOKEN = "EAAbGPDPSZAm8BRt0uBqVu1XP3s34XLvAB9JY0HzBb9AZAdQm6g8ecBlIWQSUawW72PrZCdYIUGgOiLoWlj2pnfIg1RZBV6E0sOVddiSjMavgHUTLUrI7VfPlBjtK5ZAdqpZAAtk5oySPPCuKJnoOFlw2Jj1ZCFjcbzW9JqtZCxwBO5KAVFCCDNZBobvmgZAn9Uc0yqNO0a7Q6boKZCU7aNWrkM6EdZCcT9n6JmQXS7ZCPPRoU6uaFjGZBqGqxGZB8LeJpEZD"

POST_IDS = [
    "408308732570405_1444898224349187",
]

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_CSV = ROOT / "data" / "processed" / "fb_all_comments.csv"
CHECKPOINT_FILE = ROOT / "backups" / "checkpoint_all.json"

LIMIT = 25
REQUEST_DELAY = 0.5
GRAPH_VERSION = "v19.0"
FULL_FIELDS = "id,message,created_time,like_count,comment_count,permalink_url,parent"
LIGHT_FIELDS = "id,message,created_time,like_count,comment_count"

FIELDNAMES = [
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


def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        with CHECKPOINT_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_checkpoint(data):
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CHECKPOINT_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def post_url_from_id(post_id):
    parts = post_id.split("_")
    if len(parts) == 2:
        return f"https://www.facebook.com/{parts[0]}/posts/{parts[1]}"
    return f"https://www.facebook.com/permalink/{post_id}"


def fetch_total_count(object_id, filter_value=None):
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{object_id}/comments"
    params = {
        "access_token": PAGE_ACCESS_TOKEN,
        "summary": "true",
        "limit": 0,
    }
    if filter_value:
        params["filter"] = filter_value

    try:
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
    except Exception:
        return 0

    if "error" in data:
        err = data["error"]
        print(f"\n[API ERROR] {err.get('code')} - {err.get('message')}", flush=True)
        return 0
    return data.get("summary", {}).get("total_count", 0)


def graph_error_message(response):
    if response is None:
        return ""
    try:
        return response.json().get("error", {}).get("message", "")
    except Exception:
        return response.text[:1000]


def fetch_comments_page_once(object_id, after_cursor=None, filter_value=None, limit=LIMIT, fields=FULL_FIELDS):
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{object_id}/comments"
    params = {
        "access_token": PAGE_ACCESS_TOKEN,
        "fields": fields,
        "limit": limit,
        "summary": "false",
    }
    if filter_value:
        params["filter"] = filter_value
    if after_cursor:
        params["after"] = after_cursor

    try:
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"\n[ERROR] Request failed: {exc}", flush=True)
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                print(json.dumps(response.json(), ensure_ascii=False, indent=2), flush=True)
            except Exception:
                print(response.text[:1000], flush=True)
        return [], None, False, graph_error_message(response)
    except json.JSONDecodeError:
        print("\n[ERROR] Invalid JSON response", flush=True)
        return [], None, False, "Invalid JSON response"

    if "error" in data:
        err = data["error"]
        print(f"\n[API ERROR] {err.get('code')} - {err.get('message')}", flush=True)
        return [], None, False, err.get("message", "")

    paging = data.get("paging", {})
    next_cursor = paging.get("cursors", {}).get("after") if "next" in paging else None
    return data.get("data", []), next_cursor, True, ""


def fetch_comments_page(object_id, after_cursor=None, filter_value=None):
    attempts = [
        (LIMIT, FULL_FIELDS),
        (10, LIGHT_FIELDS),
        (5, LIGHT_FIELDS),
        (1, LIGHT_FIELDS),
    ]
    last_error = ""
    for limit, fields in attempts:
        comments, next_cursor, ok, error_message = fetch_comments_page_once(
            object_id,
            after_cursor=after_cursor,
            filter_value=filter_value,
            limit=limit,
            fields=fields,
        )
        if ok:
            if fields == LIGHT_FIELDS:
                print(f"   fallback ok: limit={limit}, light fields for {object_id}", flush=True)
            return comments, next_cursor, True
        last_error = error_message
        if "reduce the amount of data" not in error_message.lower():
            break
        print(f"   fallback retry: limit={limit}, fields={'light' if fields == LIGHT_FIELDS else 'full'}", flush=True)
        time.sleep(REQUEST_DELAY)
    print(f"   fallback failed for {object_id}: {last_error}", flush=True)
    return [], None, False


def load_seen_ids_from_output():
    seen_ids = set()
    if not OUTPUT_CSV.exists():
        return seen_ids
    with OUTPUT_CSV.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            comment_id = row.get("Comment_ID", "")
            if comment_id:
                seen_ids.add(comment_id)
    return seen_ids


def parse_date(raw_time):
    try:
        dt = datetime.strptime(raw_time, "%Y-%m-%dT%H:%M:%S+0000")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return raw_time


def normalize_comment(cmt, post_url, parent_id="", level=1):
    message = cmt.get("message", "")
    parent = cmt.get("parent") or {}
    resolved_parent_id = parent.get("id", "") or parent_id
    is_deleted = message == ""

    return {
        "Date": parse_date(cmt.get("created_time", "")),
        "Comment_ID": cmt.get("id", ""),
        "Parent_ID": resolved_parent_id,
        "Level": level,
        "Content": "[deleted]" if is_deleted else message.replace("\n", " "),
        "Status": "deleted" if is_deleted else "active",
        "Reaction_Count": cmt.get("like_count", 0),
        "Reply_Count": cmt.get("comment_count", 0),
        "Permalink": cmt.get("permalink_url", ""),
        "Post_URL": post_url,
    }


def enqueue_replies(state, comment_id, level, reply_count):
    if not comment_id or int(reply_count or 0) <= 0:
        return
    queued = state.setdefault("queued_reply_ids", [])
    done = state.setdefault("done_reply_ids", [])
    queue_keys = {item["id"] for item in queued}
    done_keys = set(done)
    if comment_id in queue_keys or comment_id in done_keys:
        return
    queued.append({"id": comment_id, "level": level + 1})


def write_comment(writer, cmt, post_url, seen_ids, state, parent_id="", level=1):
    cmt_id = cmt.get("id", "")
    if not cmt_id:
        return 0

    reply_count = cmt.get("comment_count", 0)
    enqueue_replies(state, cmt_id, level, reply_count)

    if cmt_id in seen_ids:
        return 0

    writer.writerow(normalize_comment(cmt, post_url, parent_id=parent_id, level=level))
    seen_ids.add(cmt_id)
    return 1


def ensure_state(checkpoint, post_id):
    state = checkpoint.setdefault(post_id, {})
    state.setdefault("l1_after_cursor", None)
    state.setdefault("l1_done", False)
    state.setdefault("queued_reply_ids", [])
    state.setdefault("done_reply_ids", [])
    state.setdefault("skipped_reply_ids", [])
    state.setdefault("reply_after_cursors", {})
    state.setdefault("written", 0)
    return state


def crawl_top_level(post_id, post_url, state, checkpoint, writer, seen_ids):
    if state.get("l1_done"):
        return True

    total_l1 = fetch_total_count(post_id, filter_value="toplevel")
    print(f"   L1 theo API: {total_l1:,}", flush=True)
    pbar = tqdm(total=total_l1 or None, unit="cmt", desc="  L1")

    after_cursor = state.get("l1_after_cursor")
    while True:
        comments, next_cursor, ok = fetch_comments_page(post_id, after_cursor, filter_value="toplevel")
        time.sleep(REQUEST_DELAY)
        if not ok:
            pbar.close()
            return False

        added = 0
        for cmt in comments:
            added += write_comment(writer, cmt, post_url, seen_ids, state, level=1)
        state["written"] += added
        pbar.update(len(comments))

        state["l1_after_cursor"] = next_cursor
        save_checkpoint(checkpoint)

        if not next_cursor:
            state["l1_done"] = True
            save_checkpoint(checkpoint)
            break
        after_cursor = next_cursor

    pbar.close()
    return True


def crawl_reply_queue(post_id, post_url, state, checkpoint, writer, seen_ids):
    queue = deque(state.get("queued_reply_ids", []))
    done = set(state.get("done_reply_ids", []))
    skipped = set(state.get("skipped_reply_ids", []))
    after_by_id = state.setdefault("reply_after_cursors", {})
    processed_count = 0

    pbar = tqdm(total=None, unit="page", desc="  Reply pages")
    while queue:
        item = queue.popleft()
        parent_id = item["id"]
        level = item.get("level", 2)
        if parent_id in done or parent_id in skipped:
            continue

        after_cursor = after_by_id.get(parent_id)
        while True:
            replies, next_cursor, ok = fetch_comments_page(parent_id, after_cursor)
            time.sleep(REQUEST_DELAY)
            if not ok:
                print(f"   skip reply branch after fallback failed: {parent_id}", flush=True)
                skipped.add(parent_id)
                after_by_id.pop(parent_id, None)
                state["skipped_reply_ids"] = sorted(skipped)
                state["queued_reply_ids"] = list(queue)
                save_checkpoint(checkpoint)
                break

            added = 0
            for reply in replies:
                added += write_comment(
                    writer,
                    reply,
                    post_url,
                    seen_ids,
                    state,
                    parent_id=parent_id,
                    level=level,
                )
            state["written"] += added
            processed_count += 1
            pbar.update(1)

            if next_cursor:
                after_by_id[parent_id] = next_cursor
                save_checkpoint(checkpoint)
                after_cursor = next_cursor
                continue

            after_by_id.pop(parent_id, None)
            done.add(parent_id)
            state["done_reply_ids"] = sorted(done)
            state["queued_reply_ids"] = list(queue)
            save_checkpoint(checkpoint)
            break

    pbar.close()
    print(f"   Reply pages processed: {processed_count:,}", flush=True)
    return True


def crawl_post(post_id, checkpoint, writer, seen_ids):
    post_url = post_url_from_id(post_id)
    total_stream = fetch_total_count(post_id, filter_value="stream")
    print(f"\nPost: {post_url}", flush=True)
    print(f"   Stream summary.total_count: {total_stream:,} (tham khảo, không đảm bảo paging hết)", flush=True)

    state = ensure_state(checkpoint, post_id)
    ok = crawl_top_level(post_id, post_url, state, checkpoint, writer, seen_ids)
    if not ok:
        print("   Dừng ở bước L1; checkpoint đã lưu.", flush=True)
        return False

    ok = crawl_reply_queue(post_id, post_url, state, checkpoint, writer, seen_ids)
    if not ok:
        print("   Dừng ở bước replies; checkpoint đã lưu.", flush=True)
        return False

    print(f"   Xong post. Tổng dòng mới đã ghi trong state: {state.get('written', 0):,}", flush=True)
    return True


def main():
    checkpoint = load_checkpoint()
    seen_ids = load_seen_ids_from_output()
    if seen_ids:
        print(f"Existing output: {len(seen_ids):,} unique Comment_ID, will skip duplicates.", flush=True)

    file_exists = OUTPUT_CSV.exists()
    with OUTPUT_CSV.open("a", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()

        all_completed = True
        start = time.time()
        for post_id in POST_IDS:
            if not crawl_post(post_id.strip(), checkpoint, writer, seen_ids):
                all_completed = False
                break

    elapsed = time.time() - start
    print("\n" + "=" * 50, flush=True)
    print(f"Done. Output: {OUTPUT_CSV}", flush=True)
    print(f"Elapsed: {elapsed / 60:.1f} minutes", flush=True)
    print(f"Total unique rows now: {len(load_seen_ids_from_output()):,}", flush=True)
    if all_completed and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        print("Checkpoint removed.", flush=True)


if __name__ == "__main__":
    main()
