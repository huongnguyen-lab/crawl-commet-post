from playwright.sync_api import sync_playwright
import json
import time
import re
import os

POST_URL = 'https://www.facebook.com/Prudential.pva/posts/1444898224349187'
OUTPUT_FILE = f'fb_comments_{int(time.time())}.json'
USER_DATA_DIR = os.environ.get(
    'FB_USER_DATA_DIR',
    os.path.join(os.environ['LOCALAPPDATA'], 'Google', 'Chrome', 'User Data')
)

# ── MODE SELECTION ──
print("\n" + "="*50)
print("   FACEBOOK COMMENT SCRAPER")
print("="*50)
print("   1 = TEST MODE (1 button, show samples)")
print("   2 = FULL RUN (all 96K comments)")
print("="*50)
mode = input("   Choose mode (1 or 2): ").strip()

if mode == '1':
    max_depth = 1
    max_buttons = 1
    print("\n🧪 TEST MODE: 1 button, depth 1")
elif mode == '2':
    max_depth = 3
    max_buttons = 999
    print("\n🚀 FULL RUN: All buttons, depth 3")
else:
    print("Invalid choice, defaulting to TEST MODE")
    max_depth = 1
    max_buttons = 1

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=USER_DATA_DIR,
        headless=False,
    )
    page = context.new_page()
    
    all_comments = []
    seen_texts = set()
    clicked_view_all = set()
    buttons_processed = 0
    
    def switch_to_all_comments():
        try:
            for sel in ['text=Most relevant', 'text=Newest', 'text=Oldest']:
                filter_btn = page.locator(sel).first
                if filter_btn.is_visible(timeout=1000):
                    filter_btn.click()
                    time.sleep(1)
                    break
            all_options = page.locator('[role="menuitem"]').all()
            if len(all_options) >= 3:
                all_options[2].click()
                print("   ✅ Filter: All comments")
                time.sleep(2)
                return True
        except:
            pass
        return False
    
    def expand_current_thread(depth=0):
        if depth > max_depth:
            return 0
        total_clicks = 0
        while True:
            try:
                more_btn = page.locator('text=View more replies').first
                if more_btn.is_visible(timeout=500):
                    more_btn.click()
                    total_clicks += 1
                    time.sleep(0.3)
                    page.keyboard.press('End')
                else:
                    break
            except:
                break
        
        nested_btns = page.locator('text=View all').all()
        for nbtn in nested_btns:
            try:
                if not nbtn.is_visible():
                    continue
                ntext = nbtn.text_content().strip()
                if ntext in clicked_view_all:
                    continue
                print(f"      {'  '*depth}🔘 [D{depth}] {ntext}")
                clicked_view_all.add(ntext)
                nbtn.click()
                time.sleep(2)
                sub_clicks = expand_current_thread(depth + 1)
                total_clicks += sub_clicks + 1
                for _ in range(3):
                    page.keyboard.press('End')
                    time.sleep(0.2)
            except:
                pass
        return total_clicks
    
    def extract_and_beautify():
        try:
            body = page.locator('body').inner_text()
            lines = body.split('\n')
            
            post_desc = [
                'CHỌN TỪ KHÓA', 'MỞ CHỦ ĐỀ', 'Health Talk', 'Tập 2',
                'Khởi đầu nhỏ', 'khỏe chủ động', 'không gian sống',
                'Hãy cho Prudential', 'Bước 1', 'Bước 2', 'Bước 3',
                'Quà tặng hấp dẫn', 'Lưu ý:', 'Ban tổ chức',
                'Quyết định của BTC', 'All reactions', 'comments',
                'shares', 'Like', 'Comment', 'Share', 'Write a comment',
                'Bảo hiểm Nhân thọ', 'Prudential Việt Nam',
                'June 6 at', 'Top fan', 'Verified account',
                'Most relevant', 'Newest', 'Oldest', 'All comments',
                'View all', 'View more', 'See more', 'Follow',
                'May be an image', 'May be a', 'Log In', 'Sign Up',
                'Facebook', 'Notifications', 'Friend Requests',
                'Marketplace', 'Menu', 'Privacy', 'Terms', 'Help',
                'Settings', 'Find Friends', 'Forgot', 'Password',
                'Thời gian công bố', 'Ngày 22/06', 'Cách thức',
                'Thể lệ', 'Giải thưởng', 'không giới hạn',
            ]
            
            clean_lines = []
            for line in lines:
                line = line.strip()
                if not line or len(line) < 2 or len(line) > 3000:
                    continue
                if any(s.lower() in line.lower() for s in post_desc):
                    continue
                clean_lines.append(line)
            
            current_comment = None
            new_count = 0
            in_answer_block = False  # Track if we're inside a "Đáp án" block
            
            for line in clean_lines:
                is_timestamp = bool(re.match(r'^(\d+[wdhms]|just now)$', line.lower()))
                is_like_count = line.isdigit() and len(line) < 5 and line != '1' and line != '2'
                is_meta = line.lower() in ['like', 'reply', 'comment', 'share']
                
                # Check if this starts an answer block
                if line.startswith('Đáp án') or line.startswith('đáp án'):
                    in_answer_block = True
                    if current_comment and current_comment.get('text'):
                        current_comment['text'] += ' | ' + line
                    continue
                
                # Check if answer block ended (hashtag line usually ends it)
                if in_answer_block and line.startswith('#'):
                    in_answer_block = False
                    if current_comment:
                        current_comment['text'] += ' | ' + line
                    continue
                
                is_username = (
                    not is_timestamp and 
                    not is_like_count and 
                    not is_meta and
                    not in_answer_block and  # Don't treat as username if in answer block
                    len(line) >= 3 and
                    len(line) < 60 and
                    not line.startswith('#') and
                    not line.startswith('Đáp án') and
                    not line.startswith('đáp án') and
                    not re.match(r'^\d+', line) and
                    not re.match(r'^[•\-–—]', line) and
                    'View all' not in line and
                    'View more' not in line and
                    'replies' not in line.lower() and
                    'Reply to' not in line and
                    'https://' not in line and
                    'http://' not in line
                )
                
                if is_username and current_comment is None:
                    current_comment = {
                        'author': line,
                        'text': '',
                        'timestamp': '',
                        'likes': 0,
                        'replies_count': 0
                    }
                
                elif is_username and current_comment is not None:
                    if current_comment.get('text'):
                        key = f"{current_comment['author']}|{current_comment['text'][:80]}"
                        if key not in seen_texts:
                            seen_texts.add(key)
                            all_comments.append(current_comment)
                            new_count += 1
                    
                    current_comment = {
                        'author': line,
                        'text': '',
                        'timestamp': '',
                        'likes': 0,
                        'replies_count': 0
                    }
                
                elif current_comment is not None:
                    if is_timestamp and not current_comment['timestamp']:
                        current_comment['timestamp'] = line
                    elif is_like_count and not current_comment['likes']:
                        current_comment['likes'] = int(line)
                    elif not is_meta:
                        separator = ' | ' if current_comment['text'] else ''
                        current_comment['text'] += separator + line
            
            if current_comment and current_comment.get('text'):
                key = f"{current_comment['author']}|{current_comment['text'][:80]}"
                if key not in seen_texts:
                    seen_texts.add(key)
                    all_comments.append(current_comment)
                    new_count += 1
            
            return new_count
        except Exception as e:
            print(f"      Extract error: {e}")
            return 0
    
    print("\n📄 Loading post...")
    page.goto(POST_URL, wait_until='networkidle')
    time.sleep(5)
    switch_to_all_comments()
    
    for cycle in range(500):
        if buttons_processed >= max_buttons and mode == '1':
            print("\n   ✅ Test complete! (1 button processed)")
            break
            
        if 'Prudential.pva/posts' not in page.url:
            print("   🔄 Returning to post...")
            page.goto(POST_URL, wait_until='networkidle')
            time.sleep(3)
            switch_to_all_comments()
        
        for _ in range(5):
            page.keyboard.press('End')
            time.sleep(0.5)
        
        view_all_btns = page.locator('text=View all').all()
        unclicked = []
        for btn in view_all_btns:
            try:
                if btn.is_visible():
                    text = btn.text_content().strip()
                    if text not in clicked_view_all:
                        unclicked.append((btn, text))
            except:
                pass
        
        if not unclicked:
            print(f"   Cycle {cycle}: No new buttons, scrolling...")
            for _ in range(10):
                page.keyboard.press('End')
                time.sleep(1)
            continue
        
        for btn, text in unclicked:
            if buttons_processed >= max_buttons and mode == '1':
                break
                
            try:
                print(f"\n   🔘 [TOP] {text}")
                clicked_view_all.add(text)
                btn.click()
                time.sleep(2)
                total = expand_current_thread(depth=1)
                print(f"      Expansions: {total}")
                
                for _ in range(5):
                    page.keyboard.press('End')
                    time.sleep(0.2)
                
                new = extract_and_beautify()
                buttons_processed += 1
                print(f"      +{new} comments | Total: {len(all_comments):,}")
                
                with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                    json.dump(all_comments, f, ensure_ascii=False, indent=2)
                
            except Exception as e:
                print(f"      Error: {e}")
        
        print(f"   Cycle {cycle}: {len(all_comments):,} total")
    
    # ── SAVE & SHOW ──
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_comments, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*50}")
    print(f"📊 TOTAL: {len(all_comments):,} structured comments")
    print(f"💾 Saved: {OUTPUT_FILE}")
    
    if mode == '1':
        print(f"\n📋 SAMPLE COMMENTS:")
        for i, c in enumerate(all_comments[:5]):
            print(f"   [{i+1}] {c['author']}")
            print(f"       Text: {c['text'][:150]}...")
            print(f"       Time: {c['timestamp']} | Likes: {c['likes']}")
            print()
    
    context.close()
