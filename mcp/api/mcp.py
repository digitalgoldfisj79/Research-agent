"""
api/mcp.py — Vercel serverless ASGI handler for the citation-verifier MCP.

Vercel's Python runtime detects a top-level `app` variable that is an
ASGI application and routes HTTP requests to it. The route is configured
in vercel.json to forward /mcp to this handler.

The MCP server speaks the streamable-HTTP transport, which is the
remote-friendly variant of MCP (vs stdio which only works locally).

Cold start cost:
  - Importing mcp + rapidfuzz: ~200ms
  - Loading the bundled corpus: ~50ms for 10 sources
  - Total first-request latency: ~300-500ms typically

Subsequent requests on the same warm container are fast.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from rapidfuzz import fuzz
from mcp.server.fastmcp import FastMCP


# ---------- Corpus loading ----------
# In a serverless deployment we read corpus files from the deployment
# package. The directory layout is preserved from the local prototype:
#   corpus/                  (manually-curated)
#   corpus_cache/<host>/     (URL-ingested)

# When running on Vercel, the project root is the cwd. When running
# locally with `vercel dev`, same thing. Path resolution works either way
# as long as we anchor on this file's location.
HANDLER_DIR = Path(__file__).resolve().parent
ROOT = HANDLER_DIR.parent
CURATED_DIR = ROOT / "corpus"
CACHE_DIR = ROOT / "corpus_cache"

CORPUS: dict[str, dict] = {}


def _load_curated() -> None:
    if not CURATED_DIR.exists():
        return
    for p in sorted(CURATED_DIR.glob("*.txt")):
        text = p.read_text(encoding="utf-8")
        CORPUS[p.stem] = {
            "text": text,
            "meta": {"source_type": "curated", "path": str(p.relative_to(ROOT))},
        }


def _load_cache() -> None:
    if not CACHE_DIR.exists():
        return
    for host_dir in sorted(CACHE_DIR.iterdir()):
        if not host_dir.is_dir():
            continue
        manifest_path = host_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception as e:
            print(f"[startup] could not parse {manifest_path}: {e}", file=sys.stderr)
            continue
        for url, entry in manifest.get("entries", {}).items():
            slug = entry["slug"]
            text_path = host_dir / f"{slug}.txt"
            if not text_path.exists():
                continue
            text = text_path.read_text(encoding="utf-8")
            CORPUS[entry["source_id"]] = {
                "text": text,
                "meta": {
                    "source_type": "url_ingest",
                    "url": url,
                    "title": entry.get("title", ""),
                    "fetched_at": entry.get("fetched_at"),
                    "sha256": entry.get("sha256"),
                    "drift_detected_at": entry.get("drift_detected_at"),
                    "word_count": entry.get("word_count", 0),
                },
            }


_load_curated()
_load_cache()

print(f"[startup] Loaded {len(CORPUS)} sources", file=sys.stderr)


# ---------- MCP server ----------

mcp = FastMCP("citation-verifier")


@mcp.tool()
def list_sources() -> list[dict]:
    """List all available primary sources (curated + URL-ingested).

    Returns source_id, source_type (curated/url_ingest), length, and either
    a preview (curated) or title+url (url_ingest).
    """
    out = []
    for sid, rec in CORPUS.items():
        m = rec["meta"]
        entry = {
            "source_id": sid,
            "source_type": m["source_type"],
            "length_chars": len(rec["text"]),
        }
        if m["source_type"] == "url_ingest":
            entry["title"] = m.get("title", "")
            entry["url"] = m.get("url", "")
        else:
            entry["preview"] = rec["text"][:200].replace("\n", " ")
        out.append(entry)
    return out


@mcp.tool()
def search_sources(query: str, max_results: int = 5) -> list[dict]:
    """Search for paragraphs across all sources that contain ALL query terms.

    Use this to find relevant passages before quoting.
    """
    max_results = min(max(int(max_results), 1), 20)
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        return []

    results = []
    for sid, rec in CORPUS.items():
        text = rec["text"]
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        for i, para in enumerate(paras):
            lower = para.lower()
            if all(t in lower for t in terms):
                results.append({
                    "source_id": sid,
                    "paragraph_index": i,
                    "text": para[:1000],
                })
                if len(results) >= max_results:
                    return results
    return results


@mcp.tool()
def verify_quotation(quoted_text: str, source_id: str) -> dict:
    """Verify whether the given text appears in the named source.

    USE THIS BEFORE INCLUDING ANY DIRECT QUOTATION IN YOUR OUTPUT.

    Returns: verified (bool), match_type, similarity (0-100),
    closest_match (str), message (str).
    """
    if source_id not in CORPUS:
        return {
            "verified": False,
            "match_type": "unknown_source",
            "similarity": 0,
            "closest_match": None,
            "message": f"No source with id {source_id!r}. Available: {sorted(CORPUS.keys())[:20]}",
        }

    text = CORPUS[source_id]["text"]
    quoted = quoted_text.strip()
    if not quoted:
        return {
            "verified": False, "match_type": "no_match", "similarity": 0,
            "closest_match": None, "message": "Empty quotation provided.",
        }

    if quoted in text:
        return {
            "verified": True, "match_type": "exact", "similarity": 100,
            "closest_match": quoted, "message": "Exact match found in source.",
        }

    qlen = len(quoted)
    step = max(1, qlen // 6)
    best_score = 0.0
    best_window = ""
    for i in range(0, max(1, len(text) - qlen + 1), step):
        window = text[i:i + qlen]
        score = fuzz.ratio(quoted.lower(), window.lower())
        if score > best_score:
            best_score = score
            best_window = window
        if best_score == 100:
            break

    partial = fuzz.partial_ratio(quoted.lower(), text.lower())
    final_score = max(best_score, partial)

    if partial > best_score:
        for i in range(0, max(1, len(text) - qlen + 1)):
            window = text[i:i + qlen]
            s = fuzz.ratio(quoted.lower(), window.lower())
            if s >= partial - 1:
                best_window = window
                break

    if final_score >= 95:
        return {
            "verified": True, "match_type": "fuzzy",
            "similarity": float(final_score), "closest_match": best_window,
            "message": f"Near-exact match ({final_score:.0f}%). Use closest_match wording for verbatim citation.",
        }
    elif final_score >= 75:
        return {
            "verified": False, "match_type": "fuzzy",
            "similarity": float(final_score), "closest_match": best_window,
            "message": f"Approximate but not verified ({final_score:.0f}%). Use paraphrase framing or replace with closest_match wording.",
        }
    else:
        return {
            "verified": False, "match_type": "no_match",
            "similarity": float(final_score),
            "closest_match": best_window[:300] if best_window else None,
            "message": f"Quotation NOT found in source {source_id!r} (best similarity {final_score:.0f}%). Do not present this as a direct quote.",
        }


def _header_field(text: str, field: str) -> str | None:
    m = re.search(rf"^{re.escape(field)}:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else None


@mcp.tool()
def cite_passage(source_id: str, paragraph_index: int = 0) -> dict:
    """Return the canonical text of a specific paragraph + a citation string."""
    if source_id not in CORPUS:
        return {"error": f"unknown source: {source_id}"}
    rec = CORPUS[source_id]
    text = rec["text"]
    meta = rec["meta"]

    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if paragraph_index < 0 or paragraph_index >= len(paras):
        return {"error": f"paragraph_index out of range (0..{len(paras)-1})"}

    para_text = paras[paragraph_index]

    if meta["source_type"] == "url_ingest":
        title = meta.get("title") or _header_field(text, "Title") or "(no title)"
        url = meta.get("url") or _header_field(text, "Source URL") or ""
        citation = f'"{title}". {url}. [source_id={source_id}, para={paragraph_index}]'
        if meta.get("drift_detected_at"):
            citation += f' [DRIFT DETECTED at {meta["drift_detected_at"]}; re-verify]'
    else:
        author = _header_field(text, "Author") or "(unknown)"
        year = _header_field(text, "Year") or "n.d."
        title = _header_field(text, "Title") or "(no title)"
        citation = f"{author} ({year}). {title}. [source_id={source_id}, para={paragraph_index}]"

    return {
        "source_id": source_id,
        "paragraph_index": paragraph_index,
        "text": para_text,
        "citation": citation,
    }


@mcp.tool()
def source_status(source_id: str) -> dict:
    """Return metadata for a source: type, fetch time, drift status."""
    if source_id not in CORPUS:
        return {"error": f"unknown source: {source_id}"}
    return {"source_id": source_id, **CORPUS[source_id]["meta"]}


# ---------- ASGI app (Vercel entry point) ----------
#
# Vercel's Python runtime detects `app` as an ASGI application and routes
# requests to it. The MCP streamable-HTTP transport mounts on /mcp by
# default in the Starlette app it returns.

app = mcp.streamable_http_app()


# ---------- Optional API key gate ----------
#
# If the CITATION_MCP_API_KEY env var is set, require a matching
# Authorization: Bearer <key> header on every request. Without an
# API key set, the endpoint is open — fine for prototype, not for
# production.
#
# This wraps the ASGI app rather than relying on FastMCP's auth
# (which targets OAuth flows; for a personal/team deployment a
# shared secret is simpler).

REQUIRED_KEY = os.environ.get("CITATION_MCP_API_KEY")

if REQUIRED_KEY:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class APIKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # health check is open
            if request.url.path in ("/", "/health"):
                return await call_next(request)
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer "):
                return JSONResponse({"error": "missing or malformed authorization header"}, status_code=401)
            token = auth[len("Bearer "):].strip()
            if token != REQUIRED_KEY:
                return JSONResponse({"error": "invalid api key"}, status_code=403)
            return await call_next(request)

    app.add_middleware(APIKeyMiddleware)


# ---------- Health check ----------
#
# Vercel pings the root URL after deploy. Give it a useful response so
# we can verify the deployment from a browser.

from starlette.responses import JSONResponse
from starlette.routing import Route


async def health(request):
    return JSONResponse({
        "service": "citation-verifier-mcp",
        "status": "ok",
        "sources_loaded": len(CORPUS),
        "mcp_endpoint": "/mcp",
        "auth": "bearer_token" if REQUIRED_KEY else "open",
    })


# Inject the health route into the existing Starlette app
app.routes.insert(0, Route("/", health))
app.routes.insert(1, Route("/health", health))
