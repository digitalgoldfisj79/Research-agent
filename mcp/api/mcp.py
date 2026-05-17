"""
api/mcp.py — Vercel serverless ASGI handler for the citation-verifier MCP.

This server is a thin proxy + verification layer over the Supabase-backed
citation ledger. Sources live in Supabase, not in this deployment.

Architecture:
  - search_sources  → calls Supabase `search-passages` edge function
                      (semantic search via gte-small embeddings)
  - list_sources    → calls Supabase `get-sources` edge function
  - cite_passage    → calls Supabase `get-passage` edge function
  - source_status   → calls Supabase `get-sources` (single-source mode)
  - verify_quotation→ fetches passages via `get-passage` then fuzzy-matches
                      locally using rapidfuzz

The verification step deliberately runs server-side here so external
clients can't bypass it. All Supabase edge functions called are
CORS-public and read-only.

Cold start cost:
  - Importing mcp + rapidfuzz + httpx: ~300ms
  - No corpus loading (data lives in Supabase)
  - Total first-request latency: ~300-500ms typically
"""

from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import urlencode

import httpx
from rapidfuzz import fuzz
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


# ---------- Supabase configuration ----------

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://ymaqlcfjmdwncdbjprmw.supabase.co",
)
SEARCH_PASSAGES_URL = f"{SUPABASE_URL}/functions/v1/search-passages"
GET_SOURCES_URL = f"{SUPABASE_URL}/functions/v1/get-sources"
GET_PASSAGE_URL = f"{SUPABASE_URL}/functions/v1/get-passage"

# Shared httpx client. Created lazily on first use to keep cold starts fast.
_http: httpx.Client | None = None


def _client() -> httpx.Client:
    global _http
    if _http is None:
        _http = httpx.Client(timeout=20.0)
    return _http


# ---------- MCP server ----------

# FastMCP enables DNS rebinding protection by default in its
# StreamableHTTPSessionManager, which rejects requests whose Host header
# isn't in `allowed_hosts`. That protection is designed for stdio /
# localhost servers; behind a public HTTPS endpoint (Vercel here) the
# attack model doesn't apply and the check just blocks every request
# with a 421 "Invalid Host header". Disable it explicitly.

mcp = FastMCP(
    "citation-verifier",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


@mcp.tool()
def list_sources(limit: int = 50, offset: int = 0) -> dict:
    """List all available primary sources in the citation ledger.

    Returns source_id, source_type, title, source_url, and word_count for
    each source. The corpus lives in Supabase; this call fetches the
    current state at request time.

    Args:
        limit: maximum number of sources to return (default 50, max 500).
        offset: pagination offset (default 0).
    """
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    try:
        r = _client().get(GET_SOURCES_URL)
        r.raise_for_status()
        body = r.json()
        sources = body.get("sources", [])
        total = body.get("count", len(sources))
        page = sources[offset:offset + limit]
        return {"count": total, "returned": len(page), "offset": offset, "sources": page}
    except Exception as e:
        return {"error": f"failed to list sources: {e}"}


@mcp.tool()
def search_sources(query: str, max_results: int = 5,
                   similarity_threshold: float = 0.5) -> list[dict]:
    """Search the citation ledger for passages relevant to the query.

    Uses semantic search (gte-small embeddings) against all 4000+ embedded
    passages. Use this to find relevant passages before quoting.

    Args:
        query: natural language search query.
        max_results: maximum number of passages to return (1-20).
        similarity_threshold: minimum cosine similarity (0.0-1.0, default 0.5).

    Returns a list of {source_id, paragraph_index, text, similarity} dicts,
    ordered by similarity descending.
    """
    max_results = max(1, min(int(max_results), 20))
    if not (query or "").strip():
        return []
    try:
        r = _client().post(
            SEARCH_PASSAGES_URL,
            json={
                "query": query,
                "match_count": max_results,
                "similarity_threshold": float(similarity_threshold),
            },
        )
        r.raise_for_status()
        body = r.json()
        return [
            {
                "source_id": p["source_id"],
                "paragraph_index": p["paragraph_index"],
                "text": p["text"][:1000],
                "similarity": round(float(p["similarity"]), 3),
            }
            for p in body.get("results", [])
        ]
    except Exception as e:
        return [{"error": f"search failed: {e}"}]


def _fetch_passage(source_id: str, paragraph_index: int) -> dict | None:
    """Fetch a specific passage from Supabase. Returns None if not found."""
    qs = urlencode({"source_id": source_id, "paragraph_index": int(paragraph_index)})
    r = _client().get(f"{GET_PASSAGE_URL}?{qs}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _fetch_source(source_id: str) -> dict | None:
    """Fetch source metadata. Returns None if not found."""
    qs = urlencode({"source_id": source_id})
    r = _client().get(f"{GET_SOURCES_URL}?{qs}")
    r.raise_for_status()
    data = r.json()
    return data if data else None


def _fetch_all_passages(source_id: str) -> list[dict]:
    """Fetch every paragraph for a source. May be slow for large sources."""
    qs = urlencode({"source_id": source_id})
    r = _client().get(f"{GET_PASSAGE_URL}?{qs}")
    r.raise_for_status()
    body = r.json()
    return body.get("passages", []) if "passages" in body else []


@mcp.tool()
def verify_quotation(quoted_text: str, source_id: str,
                     paragraph_index: int | None = None) -> dict:
    """Verify whether the given text appears in the named source.

    USE THIS BEFORE INCLUDING ANY DIRECT QUOTATION IN YOUR OUTPUT.

    If paragraph_index is given, verifies against that single paragraph
    (fast). Otherwise verifies against ALL paragraphs in the source
    (slower for large sources but more thorough).

    Returns:
        verified (bool): True only if the quotation is a near-exact match.
        match_type (str): "exact", "fuzzy", "no_match", or "unknown_source".
        similarity (float): 0-100, rapidfuzz score.
        closest_match (str): nearest matching window in the source.
        paragraph_index (int | null): location of best match.
        message (str): human-readable explanation.
    """
    quoted = (quoted_text or "").strip()
    if not quoted:
        return {
            "verified": False, "match_type": "no_match", "similarity": 0,
            "closest_match": None, "paragraph_index": None,
            "message": "Empty quotation provided.",
        }

    try:
        if paragraph_index is not None:
            passage_resp = _fetch_passage(source_id, int(paragraph_index))
            if not passage_resp:
                return {
                    "verified": False, "match_type": "unknown_source",
                    "similarity": 0, "closest_match": None,
                    "paragraph_index": None,
                    "message": f"No passage at {source_id!r} paragraph {paragraph_index}.",
                }
            passages = [passage_resp["passage"]]
        else:
            passages = _fetch_all_passages(source_id)
            if not passages:
                return {
                    "verified": False, "match_type": "unknown_source",
                    "similarity": 0, "closest_match": None,
                    "paragraph_index": None,
                    "message": f"No source with id {source_id!r}.",
                }
    except Exception as e:
        return {
            "verified": False, "match_type": "error", "similarity": 0,
            "closest_match": None, "paragraph_index": None,
            "message": f"Failed to fetch source: {e}",
        }

    # Exact-match check first (cheap)
    for p in passages:
        if quoted in p["text"]:
            return {
                "verified": True, "match_type": "exact", "similarity": 100.0,
                "closest_match": quoted, "paragraph_index": p["paragraph_index"],
                "message": "Exact match found in source.",
            }

    # Fuzzy match across all paragraphs
    best_score = 0.0
    best_window = ""
    best_para = None
    qlen = len(quoted)
    qlower = quoted.lower()

    for p in passages:
        text = p["text"]
        partial = fuzz.partial_ratio(qlower, text.lower())
        if partial > best_score:
            best_score = partial
            best_para = p["paragraph_index"]
            # Find the actual window for closest_match
            step = max(1, qlen // 6)
            local_best_score = 0.0
            local_best_window = text[:qlen] if len(text) >= qlen else text
            for i in range(0, max(1, len(text) - qlen + 1), step):
                window = text[i:i + qlen]
                s = fuzz.ratio(qlower, window.lower())
                if s > local_best_score:
                    local_best_score = s
                    local_best_window = window
                if local_best_score == 100:
                    break
            best_window = local_best_window

    final_score = best_score

    if final_score >= 95:
        return {
            "verified": True, "match_type": "fuzzy",
            "similarity": float(final_score),
            "closest_match": best_window,
            "paragraph_index": best_para,
            "message": f"Near-exact match ({final_score:.0f}%). Use closest_match wording for verbatim citation.",
        }
    elif final_score >= 75:
        return {
            "verified": False, "match_type": "fuzzy",
            "similarity": float(final_score),
            "closest_match": best_window,
            "paragraph_index": best_para,
            "message": f"Approximate but not verified ({final_score:.0f}%). Use paraphrase framing or replace with closest_match wording.",
        }
    else:
        return {
            "verified": False, "match_type": "no_match",
            "similarity": float(final_score),
            "closest_match": (best_window[:300] if best_window else None),
            "paragraph_index": best_para,
            "message": f"Quotation NOT found in source {source_id!r} (best similarity {final_score:.0f}%). Do not present this as a direct quote.",
        }


@mcp.tool()
def cite_passage(source_id: str, paragraph_index: int = 0) -> dict:
    """Return the canonical text of a specific paragraph plus a citation string.

    Args:
        source_id: the source identifier (from list_sources or search_sources).
        paragraph_index: 0-based paragraph index within the source.

    Returns dict with: source_id, paragraph_index, text, citation, source_url, title.
    """
    try:
        passage_resp = _fetch_passage(source_id, int(paragraph_index))
    except Exception as e:
        return {"error": f"failed to fetch passage: {e}"}
    if not passage_resp:
        return {"error": f"no passage at {source_id!r} paragraph {paragraph_index}"}

    src = passage_resp["source"]
    pas = passage_resp["passage"]
    title = src.get("title") or "(no title)"
    url = src.get("source_url") or ""

    citation = (
        f'"{title}". {url}. [source_id={source_id}, para={paragraph_index}]'
        if src.get("source_type") == "url_ingest"
        else f"{title}. [source_id={source_id}, para={paragraph_index}]"
    )

    return {
        "source_id": source_id,
        "paragraph_index": paragraph_index,
        "text": pas["text"],
        "citation": citation,
        "source_url": url,
        "title": title,
    }


@mcp.tool()
def source_status(source_id: str) -> dict:
    """Return metadata for a single source: type, URL, title, fetch time, word count, sha256."""
    try:
        src = _fetch_source(source_id)
    except Exception as e:
        return {"error": f"failed to fetch source: {e}"}
    if not src:
        return {"error": f"unknown source: {source_id}"}
    return src


# ---------- ASGI app (Vercel entry point) ----------

app = mcp.streamable_http_app()


# ---------- Optional API key gate ----------

REQUIRED_KEY = os.environ.get("CITATION_MCP_API_KEY")

if REQUIRED_KEY:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class APIKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
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


# ---------- TrustedHost middleware patch ----------
#
# FastMCP's streamable_http_app() installs starlette TrustedHostMiddleware
# with localhost-only defaults, which is appropriate for stdio but
# rejects every request when deployed behind a public hostname (Vercel,
# Cloudflare etc) with a 421 "Invalid Host header". Vercel handles
# routing/protection at the edge; trusting hosts at the app layer adds
# no real security in this deployment. Strip it out.

try:
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    app.user_middleware = [
        m for m in app.user_middleware
        if getattr(m, "cls", None) is not TrustedHostMiddleware
    ]
    # Force the middleware stack to be rebuilt on next request
    app.middleware_stack = None
except Exception as _e:
    print(f"[startup] warning: could not patch TrustedHostMiddleware: {_e}", file=sys.stderr)


# ---------- Health check ----------

from starlette.responses import JSONResponse
from starlette.routing import Route


async def health(request):
    sources_count: int | str = "unknown"
    try:
        r = _client().get(GET_SOURCES_URL, timeout=10.0)
        if r.status_code == 200:
            sources_count = r.json().get("count", "unknown")
    except Exception:
        sources_count = "supabase-unreachable"

    return JSONResponse({
        "service": "citation-verifier-mcp",
        "status": "ok",
        "sources_loaded": sources_count,
        "backend": "supabase",
        "supabase_url": SUPABASE_URL,
        "mcp_endpoint": "/mcp",
        "auth": "bearer_token" if REQUIRED_KEY else "open",
    })


app.routes.insert(0, Route("/", health))
app.routes.insert(1, Route("/health", health))
