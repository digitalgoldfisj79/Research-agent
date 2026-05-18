"""Voynich.ninja resumable crawler — archive view, per-post chunking, filter+citation.

State: checkpoint.json tracks completed threads. Resumable.
Output: writes source row + passages directly to Supabase via ingest-passages.

Usage: python3 ninja_crawler.py <forum_id> [<forum_id> ...]
"""
import re, json, time, sys, os, urllib.request, urllib.error
from datetime import datetime

UA = "Mozilla/5.0 (research-agent corpus build / voynich-ninja archive crawl)"
INGEST_SECRET = "research-agent-prototype-2026"
INGEST_ENDPOINT = "https://ymaqlcfjmdwncdbjprmw.supabase.co/functions/v1/ingest-passages"
SUPABASE_URL = "https://ymaqlcfjmdwncdbjprmw.supabase.co"

MIN_POST_WORDS = 30
MAX_PASSAGE_WORDS = 300
RATE_LIMIT_SECONDS = 0.6  # ~1.6 req/sec polite

CHECKPOINT_FILE = "ninja_checkpoint.json"
INDEX_FILE = "voynich_ninja_thread_index.json"

# Load thread index
with open(INDEX_FILE) as f:
    INDEX = json.load(f)

# Load or initialize checkpoint
if os.path.exists(CHECKPOINT_FILE):
    with open(CHECKPOINT_FILE) as f:
        CHECKPOINT = json.load(f)
else:
    CHECKPOINT = {"completed_threads": [], "passages_inserted": 0, "started_at": datetime.now().isoformat()}

def save_checkpoint():
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(CHECKPOINT, f, indent=2)

def fetch(url, retries=2):
    """HTTP GET with retry, returns text or raises."""
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
        time.sleep(2)
    raise last_e

def extract_thread_title(html):
    m = re.search(r'<title>The Voynich Ninja - ([^<]+)</title>', html)
    if m:
        return m.group(1).strip()
    return "Untitled"

def extract_posts(html):
    """Extract list of (author, dateline, message_text) from MyBB archive HTML.
    Strips blockquotes (replies quoting earlier posts) and HTML chrome."""
    # First strip blockquoted replies — these are duplicates of earlier posts
    html_clean = re.sub(r'<blockquote class="mycode_quote">.*?</blockquote>', ' [...] ', html, flags=re.DOTALL)
    # Each post: <div class="author"><h2><a ...>NAME</a></h2></div><div class="dateline">DATE</div>...<div class="message">CONTENT</div>
    post_blocks = re.findall(
        r'<div class="author"><h2><a [^>]*>([^<]+)</a></h2></div>\s*<div class="dateline">([^<]+)</div>\s*</div>\s*<div class="message">(.*?)</div>',
        html_clean, flags=re.DOTALL
    )
    out = []
    for author, dateline, message_html in post_blocks:
        # Strip remaining tags
        t = re.sub(r'<[^>]+>', ' ', message_html)
        t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
               .replace("&lt;", "<").replace("&gt;", ">")
               .replace("&quot;", '"').replace("&#39;", "'"))
        t = re.sub(r'\s+', ' ', t).strip()
        if t:
            out.append({"author": author.strip(), "dateline": dateline.strip(), "text": t})
    return out

def chunk_post(text, max_words=MAX_PASSAGE_WORDS):
    """Split a long post into sentence-bounded chunks of <= max_words."""
    words = text.split()
    if len(words) <= max_words:
        return [text]
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    chunks, cur, cw = [], [], 0
    for s in sentences:
        sw = len(s.split())
        if cw + sw > max_words and cur:
            chunks.append(" ".join(cur))
            cur = [s]
            cw = sw
        else:
            cur.append(s)
            cw += sw
    if cur:
        chunks.append(" ".join(cur))
    return chunks

def fetch_full_thread(tid):
    """Fetch all pages of a thread (archive view). Returns (title, posts list)."""
    url1 = f"https://www.voynich.ninja/archive/index.php/{tid}.html"
    html1 = fetch(url1)
    if not html1:
        return None, []
    title = extract_thread_title(html1)
    posts = extract_posts(html1)
    # Find pagination
    page_links = re.findall(rf'href="https://www\.voynich\.ninja/archive/index\.php/{tid}-(\d+)\.html"', html1)
    max_page = max([int(p) for p in page_links], default=1)
    for p in range(2, max_page + 1):
        url = f"https://www.voynich.ninja/archive/index.php/{tid}-{p}.html"
        try:
            htmlp = fetch(url)
            if htmlp:
                posts.extend(extract_posts(htmlp))
        except Exception as e:
            print(f"  [{tid}] page {p} error: {e}")
        time.sleep(RATE_LIMIT_SECONDS)
    return title, posts

def upsert_source(source_id, title, source_url, source_citation, word_count):
    """Insert source row via direct Supabase REST API (we use service role here)."""
    # Actually use SQL via ingest-passages-friendly route — for simplicity, batch source upserts
    # at end via SQL. For now, accumulate into a queue.
    return  # handled separately

def post_to_ingest(source_id, passages):
    """POST passages to ingest-passages with retry. Returns (inserted, failed_batches)."""
    total_inserted = 0
    failed_batches = 0
    # Adaptive batch size: smaller batches for long passages to avoid edge function timeouts
    i = 0
    while i < len(passages):
        # Choose batch size: 3 by default, 1 if next passage is very long
        first_wc = len(passages[i]["text"].split())
        BATCH = 1 if first_wc > 500 else (2 if first_wc > 200 else 3)
        batch = passages[i:i+BATCH]
        payload = {"passages": [
            {"source_id": source_id, "paragraph_index": p["idx"], "text": p["text"]}
            for p in batch
        ]}
        body = json.dumps(payload).encode()
        ok = False
        for attempt in range(4):  # 1 + 3 retries
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
                    time.sleep(2 ** attempt)  # 1s, 2s, 4s backoff
                else:
                    print(f"  [ingest] {source_id} batch {i//BATCH+1} failed after retries: {str(e)[:120]}")
        if not ok:
            failed_batches += 1
        time.sleep(0.3)
        i += BATCH
    return total_inserted, failed_batches

# Now the main loop
def process_thread(forum_id, tid, forum_name):
    if tid in CHECKPOINT["completed_threads"]:
        return 0, "skip"
    try:
        title, posts = fetch_full_thread(tid)
    except Exception as e:
        print(f"  [{tid}] fetch failed: {str(e)[:120]}")
        return 0, "error"
    if not title:
        return 0, "missing"
    # Filter posts
    kept = []
    for p in posts:
        wc = len(p["text"].split())
        if wc < MIN_POST_WORDS:
            continue
        kept.append(p)
    if not kept:
        CHECKPOINT["completed_threads"].append(tid)
        save_checkpoint()
        return 0, "empty"
    # Build passages: one per post (chunked if long)
    passages = []
    idx = 0
    for p in kept:
        author = p["author"]
        dateline = p["dateline"]
        prefix = f"[{author}, {dateline}] "
        for chunk in chunk_post(p["text"]):
            passages.append({"idx": idx, "text": prefix + chunk})
            idx += 1
    # Source row info to record after success
    source_id = f"voynich_ninja_{tid}"
    source_url = f"https://www.voynich.ninja/{tid}.html"
    citation = f'Voynich.ninja forum, {forum_name}: "{title}". {source_url}. Accessed {datetime.now().date()}.'
    
    # Insert source row first via SQL (using the upsert SQL bundle we accumulate)
    # For now, ingest-passages will create the source FK reference; we need to ensure the source row exists.
    # Simplest path: include a post to a small "upsert source row" SQL via a separate endpoint.
    # But we don't have such an endpoint. Use the supabase REST API directly with service role? No — that's not exposed safely.
    # Cleanest: collect source rows in a JSON file, batch-insert via SQL at end of each batch.
    
    # Upsert source row first (FK constraint requires source exists)
    source_payload = {
        "source_id": source_id,
        "title": title[:500],
        "source_url": source_url,
        "source_citation": citation,
        "source_type": "forum_thread",
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
    except Exception as e:
        print(f"  [{tid}] source upsert failed: {str(e)[:120]}")
        return 0, "source_fail"
    
    inserted, failed_batches = post_to_ingest(source_id, passages)
    if failed_batches > 0:
        # Mark partial-fail: don't mark complete so retry will re-process
        print(f"  [{tid}] partial fail: {inserted}/{len(passages)} inserted, {failed_batches} batches failed")
        return inserted, "partial"
    if inserted == 0:
        return 0, "no_insert"
    
    CHECKPOINT["completed_threads"].append(tid)
    CHECKPOINT["passages_inserted"] += inserted
    # Record source metadata for end-of-batch SQL upsert
    SOURCE_QUEUE.append({
        "source_id": source_id,
        "title": title[:500],
        "source_url": source_url,
        "source_citation": citation,
        "source_type": "forum_thread",
        "license": "Fair use for academic citation indexing. Individual post copyright remains with their authors; full text stored for indexing only, short verbatim quotes used for citation per fair-use doctrine.",
        "word_count": sum(len(p["text"].split()) for p in passages),
        "forum_id": forum_id,
        "forum_name": forum_name,
    })
    save_checkpoint()
    return inserted, "ok"

SOURCE_QUEUE = []

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 ninja_crawler.py <forum_id> [<forum_id> ...] [--limit N]")
        sys.exit(1)
    
    limit = None
    forum_ids = []
    for arg in sys.argv[1:]:
        if arg.startswith("--limit"):
            limit = int(sys.argv[sys.argv.index(arg)+1])
        elif arg.isdigit():
            forum_ids.append(int(arg))
    
    threads_to_process = []
    for fid in forum_ids:
        info = INDEX[str(fid)]
        for tid in info["threads"]:
            threads_to_process.append((fid, tid, info["name"]))
    
    if limit:
        threads_to_process = threads_to_process[:limit]
    
    print(f"Threads queued: {len(threads_to_process)}")
    print(f"Already completed: {len(CHECKPOINT['completed_threads'])}")
    print()
    
    counts = {"ok": 0, "skip": 0, "error": 0, "empty": 0, "missing": 0, "no_insert": 0, "partial": 0, "source_fail": 0}
    t0 = time.time()
    for i, (fid, tid, fname) in enumerate(threads_to_process):
        inserted, status = process_thread(fid, tid, fname)
        counts[status] += 1
        elapsed = time.time() - t0
        rate = (i+1) / elapsed if elapsed > 0 else 0
        if i % 5 == 0 or status != "skip":
            print(f"  [{i+1}/{len(threads_to_process)}] {tid} ({fname[:25]}) -> {status}, +{inserted} passages, total {CHECKPOINT['passages_inserted']}, rate={rate:.2f}/s")
    
    # Write source queue
    with open("ninja_source_queue.json", "w") as f:
        json.dump(SOURCE_QUEUE, f, indent=2)
    
    print()
    print(f"Counts: {counts}")
    print(f"Source rows to upsert: {len(SOURCE_QUEUE)}")
    print(f"Total passages inserted this run: {sum([s.get('word_count', 0) for s in SOURCE_QUEUE])} words distributed across {CHECKPOINT['passages_inserted']} passages")
