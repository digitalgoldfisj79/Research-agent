"""Parallel version: 3 workers process threads concurrently."""
import re, json, time, sys, os, urllib.request, urllib.error, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

UA = "Mozilla/5.0 (research-agent corpus build / voynich-ninja archive crawl)"
INGEST_SECRET = "research-agent-prototype-2026"
INGEST_ENDPOINT = "https://ymaqlcfjmdwncdbjprmw.supabase.co/functions/v1/ingest-passages"
SUPABASE_URL = "https://ymaqlcfjmdwncdbjprmw.supabase.co"

MIN_POST_WORDS = 30
MAX_PASSAGE_WORDS = 300
PAGE_DELAY = 0.3  # per-worker delay between archive pages
WORKERS = 3

CHECKPOINT_FILE = "ninja_checkpoint.json"
INDEX_FILE = "voynich_ninja_thread_index.json"

cp_lock = threading.Lock()
with open(INDEX_FILE) as f:
    INDEX = json.load(f)

if os.path.exists(CHECKPOINT_FILE):
    with open(CHECKPOINT_FILE) as f:
        CHECKPOINT = json.load(f)
else:
    CHECKPOINT = {"completed_threads": [], "passages_inserted": 0, "started_at": datetime.now().isoformat()}

def save_checkpoint():
    with cp_lock:
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(CHECKPOINT, f, indent=2)

def fetch(url, retries=2):
    last_e = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last_e = e
        except Exception as e:
            last_e = e
        time.sleep(1 + attempt)
    raise last_e

def extract_thread_title(html):
    m = re.search(r'<title>The Voynich Ninja - ([^<]+)</title>', html)
    return m.group(1).strip() if m else "Untitled"

def extract_posts(html):
    html_clean = re.sub(r'<blockquote class="mycode_quote">.*?</blockquote>', ' [...] ', html, flags=re.DOTALL)
    post_blocks = re.findall(
        r'<div class="author"><h2><a [^>]*>([^<]+)</a></h2></div>\s*<div class="dateline">([^<]+)</div>\s*</div>\s*<div class="message">(.*?)</div>',
        html_clean, flags=re.DOTALL
    )
    out = []
    for author, dateline, message_html in post_blocks:
        t = re.sub(r'<[^>]+>', ' ', message_html)
        t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
               .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
        t = re.sub(r'\s+', ' ', t).strip()
        if t:
            out.append({"author": author.strip(), "dateline": dateline.strip(), "text": t})
    return out

def chunk_post(text, max_words=MAX_PASSAGE_WORDS):
    words = text.split()
    if len(words) <= max_words:
        return [text]
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    chunks, cur, cw = [], [], 0
    for s in sentences:
        sw = len(s.split())
        if cw + sw > max_words and cur:
            chunks.append(" ".join(cur)); cur = [s]; cw = sw
        else:
            cur.append(s); cw += sw
    if cur: chunks.append(" ".join(cur))
    return chunks

def fetch_full_thread(tid):
    url1 = f"https://www.voynich.ninja/archive/index.php/{tid}.html"
    html1 = fetch(url1)
    if not html1:
        return None, []
    title = extract_thread_title(html1)
    posts = extract_posts(html1)
    page_links = re.findall(rf'href="https://www\.voynich\.ninja/archive/index\.php/{tid}-(\d+)\.html"', html1)
    max_page = max([int(p) for p in page_links], default=1)
    for p in range(2, max_page + 1):
        url = f"https://www.voynich.ninja/archive/index.php/{tid}-{p}.html"
        time.sleep(PAGE_DELAY)
        try:
            htmlp = fetch(url)
            if htmlp:
                posts.extend(extract_posts(htmlp))
        except: pass
    return title, posts

def post_to_ingest(source_id, passages):
    total_inserted = 0
    failed_batches = 0
    i = 0
    while i < len(passages):
        first_wc = len(passages[i]["text"].split())
        BATCH = 1 if first_wc > 500 else (2 if first_wc > 200 else 3)
        batch = passages[i:i+BATCH]
        payload = {"passages": [
            {"source_id": source_id, "paragraph_index": p["idx"], "text": p["text"]}
            for p in batch
        ]}
        body = json.dumps(payload).encode()
        ok = False
        for attempt in range(4):
            try:
                req = urllib.request.Request(
                    INGEST_ENDPOINT, data=body,
                    headers={"Content-Type": "application/json", "x-ingest-secret": INGEST_SECRET},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as r:
                    res = json.loads(r.read())
                    total_inserted += res.get("inserted", 0)
                ok = True
                break
            except Exception as e:
                if attempt < 3:
                    time.sleep(2 ** attempt)
        if not ok:
            failed_batches += 1
        time.sleep(0.15)
        i += BATCH
    return total_inserted, failed_batches

def process_thread(forum_id, tid, forum_name):
    with cp_lock:
        if tid in CHECKPOINT["completed_threads"]:
            return tid, 0, "skip"
    try:
        title, posts = fetch_full_thread(tid)
    except Exception as e:
        return tid, 0, f"fetch_err"
    if not title:
        return tid, 0, "missing"
    kept = [p for p in posts if len(p["text"].split()) >= MIN_POST_WORDS]
    if not kept:
        with cp_lock:
            CHECKPOINT["completed_threads"].append(tid)
        save_checkpoint()
        return tid, 0, "empty"
    passages = []
    idx = 0
    for p in kept:
        prefix = f"[{p['author']}, {p['dateline']}] "
        for chunk in chunk_post(p["text"]):
            passages.append({"idx": idx, "text": prefix + chunk})
            idx += 1
    
    source_id = f"voynich_ninja_{tid}"
    source_url = f"https://www.voynich.ninja/{tid}.html"
    citation = f'Voynich.ninja forum, {forum_name}: "{title}". {source_url}. Accessed {datetime.now().date()}.'
    
    source_payload = {
        "source_id": source_id, "title": title[:500], "source_url": source_url,
        "source_citation": citation, "source_type": "forum_thread",
        "license": "Voynich.ninja public forum content. Fair use for academic citation indexing; individual post copyright remains with their authors.",
        "word_count": sum(len(p["text"].split()) for p in passages),
    }
    try:
        body = json.dumps(source_payload).encode()
        req = urllib.request.Request(
            f"{SUPABASE_URL}/functions/v1/upsert-source", data=body,
            headers={"Content-Type": "application/json", "x-ingest-secret": INGEST_SECRET},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=30).read()
    except:
        return tid, 0, "source_fail"
    
    inserted, failed = post_to_ingest(source_id, passages)
    if failed > 0:
        return tid, inserted, "partial"
    if inserted == 0:
        return tid, 0, "no_insert"
    with cp_lock:
        CHECKPOINT["completed_threads"].append(tid)
        CHECKPOINT["passages_inserted"] += inserted
    save_checkpoint()
    return tid, inserted, "ok"

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 ninja_crawler_parallel.py <forum_id> [<forum_id> ...]")
        sys.exit(1)
    
    forum_ids = [int(a) for a in sys.argv[1:] if a.isdigit()]
    queue = []
    for fid in forum_ids:
        info = INDEX[str(fid)]
        for tid in info["threads"]:
            queue.append((fid, tid, info["name"]))
    
    print(f"Threads queued: {len(queue)}, workers: {WORKERS}")
    print(f"Already completed: {len(CHECKPOINT['completed_threads'])}")
    
    counts = {"ok": 0, "skip": 0, "fetch_err": 0, "empty": 0, "missing": 0, "no_insert": 0, "partial": 0, "source_fail": 0}
    t0 = time.time()
    processed = 0
    
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(process_thread, fid, tid, name): tid for fid, tid, name in queue}
        for fut in as_completed(futures):
            tid, inserted, status = fut.result()
            counts[status] = counts.get(status, 0) + 1
            processed += 1
            elapsed = time.time() - t0
            rate = processed / elapsed if elapsed else 0
            if processed % 10 == 0 or status not in ("skip", "ok"):
                print(f"  [{processed}/{len(queue)}] {tid} -> {status}, +{inserted}, total {CHECKPOINT['passages_inserted']}, rate={rate:.2f}/s, ok={counts['ok']} partial={counts.get('partial',0)}")
    
    print(f"\nFinal counts: {counts}")
    print(f"Elapsed: {time.time() - t0:.0f}s")
