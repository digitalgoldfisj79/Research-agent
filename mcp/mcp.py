"""
api/mcp.py — Vercel serverless ASGI handler for the citation-verifier MCP.

This server is a thin proxy + verification layer over the Supabase-backed
citation ledger. Sources live in Supabase, not in this deployment.

Architecture:
  - search_sources         → calls Supabase `search-passages` edge function
                             (now hybrid v3: vector + FTS via RRF, with
                             recency bonus and refs_html penalty)
  - list_sources           → calls Supabase `get-sources` edge function
  - cite_passage           → calls Supabase `get-passage` edge function
  - source_status          → calls Supabase `get-sources` (single-source mode)
  - verify_quotation       → fetches passages via `get-passage` then fuzzy-matches
                             locally using rapidfuzz
  - trace_claim_history    → calls Supabase `trace-claim-history` edge function
                             (LLM historiography with verification gate)
  - ask_research_question  → calls Supabase `research` edge function
                             (unified router: lookup/claim_history/qa with
                             LLM query expansion, hybrid retrieval, synthesis,
                             and recency awareness)

The verification step deliberately runs server-side here so external
clients can't bypass it. All Supabase edge functions called are
CORS-public and read-only.
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

SEARCH_PASSAGES_URL     = f"{SUPABASE_URL}/functions/v1/search-passages"
GET_SOURCES_URL         = f"{SUPABASE_URL}/functions/v1/get-sources"
GET_PASSAGE_URL         = f"{SUPABASE_URL}/functions/v1/get-passage"
TRACE_CLAIM_HISTORY_URL = f"{SUPABASE_URL}/functions/v1/trace-claim-history"
RESEARCH_URL            = f"{SUPABASE_URL}/functions/v1/research"
GRAPH_QUERY_URL         = f"{SUPABASE_URL}/functions/v1/graph-query"


# Shared httpx client. Created lazily on first use to keep cold starts fast.
_http: httpx.Client | None = None


def _client() -> httpx.Client:
    global _http
    if _http is None:
        _http = httpx.Client(timeout=20.0)
    return _http


# ---------- MCP server ----------

mcp = FastMCP(
    "citation-verifier",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


@mcp.tool()
def list_sources(limit: int = 50, offset: int = 0) -> dict:
    """List all available primary sources in the citation ledger."""
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

    Backed by Supabase `search-passages` v2 (hybrid retrieval via
    `search_passages_hybrid_v3` RPC). Each result now includes
    `passage_year` and `passage_author` when extractable, plus
    `vector_rank` and `fts_rank_int` retrieval diagnostics.
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
                "passage_year": p.get("passage_year"),
                "passage_author": p.get("passage_author"),
                "vector_rank": p.get("vector_rank"),
                "fts_rank_int": p.get("fts_rank_int"),
            }
            for p in body.get("results", [])
        ]
    except Exception as e:
        return [{"error": f"search failed: {e}"}]


def _fetch_passage(source_id: str, paragraph_index: int) -> dict | None:
    qs = urlencode({"source_id": source_id, "paragraph_index": int(paragraph_index)})
    r = _client().get(f"{GET_PASSAGE_URL}?{qs}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _fetch_source(source_id: str) -> dict | None:
    qs = urlencode({"source_id": source_id})
    r = _client().get(f"{GET_SOURCES_URL}?{qs}")
    r.raise_for_status()
    data = r.json()
    return data if data else None


def _fetch_all_passages(source_id: str) -> list[dict]:
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

    for p in passages:
        if quoted in p["text"]:
            return {
                "verified": True, "match_type": "exact", "similarity": 100.0,
                "closest_match": quoted, "paragraph_index": p["paragraph_index"],
                "message": "Exact match found in source.",
            }

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
    """Return the canonical text of a specific paragraph plus a citation string."""
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


# ---------- trace_claim_history ----------

@mcp.tool()
def trace_claim_history(claim: str, include_passages: bool = False) -> dict:
    """Trace the historiographic evolution of a claim about the Voynich Manuscript.

    This is a DIFFERENT question shape from search_sources or verify_quotation.
    Use this tool when the user wants to know HOW A CLAIM HAS BEEN ARGUED
    THROUGH TIME — who first proposed it, what evidence was presented, how
    it has evolved, current status — rather than whether the claim is true.

    The tool calls a downstream LLM that synthesises retrieved passages into
    a chronological historiography. Every assertion the LLM makes is then
    checked by a verification gate against the retrieved corpus: author names,
    years, percentages, and supporting-passage indices that can't be confirmed
    are returned in the `verification.unverified_assertions` array.

    Latency note: this call typically takes 30–45 seconds (measured) because it makes
    two LLM passes (classify + synthesise) plus the verification scan.

    Args:
      claim: the claim to trace (min 8 characters). Best results for advocacy
        claims about origin, authorship, dating, or interpretation.
        Descriptive/empirical claims will produce earliest_advocacy=null
        with a caveat.
      include_passages: if True, include the full chronological_evidence
        (50-item summary) and retrieved_passages_full (50 full passage
        texts). Default False keeps the response compact (~10-20 KB).
        Set True if you need to inspect specific passages cited.

    Returns dict with:
      retrieval_summary: {total_retrieved, dated_count, undated_count, year_range}
      historiography:
        earliest_advocacy: {passage_year, author, verbatim_quote,
                            supporting_passage_index, source_id, caveat} | null
        evolution_summary: prose tracing claim through time
        current_status: {label, reasoning, modern_advocates[], modern_rejecters[]}
          where label ∈ {currently_held, historically_proposed, contested,
                         abandoned, insufficient_corpus}
        unresolved_historical_disputes: list of disagreements about what
          historical figures believed
        integrated_narrative: 1-2 paragraphs of historiographic prose
      verification:
        unverified_assertions: list of LLM claims the gate couldn't confirm
          (with field, status, value, supporting_passage_index)
        flagged_specifics: years and percentages in narrative, each marked
          verified_in_corpus or not_in_any_retrieved_passage
        summary: counts by category
        filtered_names: forum handles / emails the post-process dropped
      usage: {total_tokens, retrieved}
    """
    claim = (claim or "").strip()
    if len(claim) < 8:
        return {"error": "claim required (min 8 chars)"}

    try:
        # Per-call timeout: measured 30-42s across 4 test claims;
        # 120s allows ~3x headroom. Vercel's default maxDuration of 60s
        # is sufficient at this latency.
        r = _client().post(
            TRACE_CLAIM_HISTORY_URL,
            json={"claim": claim},
            timeout=120.0,  # measured 30-42s; 120s gives ~3x headroom
        )
        r.raise_for_status()
        body = r.json()
        if not include_passages:
            body.pop("chronological_evidence", None)
            body.pop("retrieved_passages_full", None)
        return body
    except httpx.TimeoutException:
        return {"error": "trace-claim-history timed out (>120s). Try a more specific claim."}
    except Exception as e:
        return {"error": f"trace-claim-history failed: {e}"}


# ---------- NEW: ask_research_question ----------

@mcp.tool()
def ask_research_question(query: str, mode_override: str | None = None) -> dict:
    """Ask a research question about the Voynich Manuscript and get a synthesised,
    cited answer drawn from the corpus of voynich.ninja forum threads, researcher
    blogs (Pelling, O'Donovan), academic papers, and reference sites (voynich.nu).

    USE THIS for complete research questions that benefit from a synthesised
    answer with citations. Examples:
      - "What is the recent LSA work by Davis and Layfield?"
      - "Who first proposed the slot grammar?"
      - "How has the forgery hypothesis evolved over time?"
      - "What does the corpus say about the f116v marginalia?"

    PREFER OTHER TOOLS when you need granular operations:
      - search_sources: raw passage retrieval to inspect yourself
      - verify_quotation: check whether a specific quote appears in a source
      - cite_passage: fetch the full text of one specific paragraph
      - trace_claim_history: chronological evolution of a tracked claim
        (use the dedicated tool when the user explicitly asks for historiography
         and the claim matches one of the tracked vocabulary entries)

    The endpoint auto-routes to one of three internal modes:
      - lookup: short noun-phrase queries -> direct passage list
      - claim_history: queries matching tracked claim vocabulary -> trace
      - qa: open-ended questions -> cited synthesised answer

    Recency-aware: if the query mentions "recent", "latest", "this year",
    "last week" etc., the synthesiser leads with the most recent dated
    passage from the topic principal.

    Args:
      query: the research question (min 4 chars).
      mode_override: optional, one of "lookup", "claim_history", "qa" to
        force a specific routing decision. Default None lets the router
        choose.

    Returns the full /research envelope. Most useful fields:
      - mode: which routing mode was selected
      - payload.answer: the synthesised text answer (for qa mode)
      - payload.summary: one-sentence summary (for qa mode)
      - payload.citations: array of {source_id, paragraph_index, passage_year,
        passage_author, snippet, rrf_score}
      - payload.recency_intent: true if recency detection fired
      - total_elapsed_ms: end-to-end latency

    Latency: typically 12-25 seconds, up to 60 seconds under heavy load.
    """
    query = (query or "").strip()
    if len(query) < 4:
        return {"error": "query required (min 4 chars)"}

    body: dict = {"query": query}
    if mode_override in ("lookup", "claim_history", "qa"):
        body["mode_override"] = mode_override

    try:
        # /research has an internal 85s deadline; 90s here gives a small
        # transport-overhead buffer without exceeding Vercel maxDuration: 60.
        # Note: if Vercel maxDuration ever needs to accommodate this fully,
        # bump vercel.json to maxDuration: 90.
        r = _client().post(
            RESEARCH_URL,
            json=body,
            timeout=90.0,
        )
        r.raise_for_status()
        return r.json()
    except httpx.TimeoutException:
        return {
            "error": "research endpoint timed out (>90s). Try a more specific query or check Supabase status."
        }
    except httpx.HTTPStatusError as e:
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text[:500]
        return {
            "error": f"research endpoint HTTP {e.response.status_code}",
            "detail": detail,
        }
    except Exception as e:
        return {"error": f"research endpoint failed: {e}"}




# ---------- graph-query tools (structural analysis of the citation graph) ----------
#
# These four tools expose the underlying claim/edge/passage graph for analytic
# queries — different in shape from search_sources (retrieval) and
# ask_research_question (synthesis). All call a single read-only Supabase edge
# function (graph-query) that routes by action.


def _graph_query(action: str, **kwargs) -> dict:
    """Internal helper. POSTs to graph-query edge function with given action+params."""
    try:
        body = {"action": action, **{k: v for k, v in kwargs.items() if v is not None}}
        r = _client().post(GRAPH_QUERY_URL, json=body, timeout=30.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text[:500]
        return {"error": f"graph-query HTTP {e.response.status_code}", "detail": detail}
    except Exception as e:
        return {"error": f"graph-query failed: {e}"}


@mcp.tool()
def list_claims() -> dict:
    """List all hypotheses tracked in the citation graph's claim vocabulary.

    Each claim has: claim_id, display_name, description, example_phrasings
    (alternative wordings the extractor recognises), related_claim_ids
    (cross-references), and edge_count (how many passages have been
    stance-classified against this claim across the corpus).

    Use this to discover what hypotheses the graph currently tracks before
    using query_edges or author_cooccurrence to drill in.
    """
    return _graph_query("list_claims")


@mcp.tool()
def query_edges(
    claim_id: str | None = None,
    source_id: str | None = None,
    passage_author_id: str | None = None,
    nested_author_id: str | None = None,
    stance: str | None = None,
    min_confidence: float | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    extraction_version: str | None = None,
    include_text: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Filter the passage_claim_edges graph by structural criteria.

    Each edge is a single passage's stance on a single tracked claim with a
    confidence score and optional nested attribution (cited authority).

    Args:
      claim_id: restrict to one claim (e.g. "forgery_hypothesis"). Use
        list_claims to discover valid ids.
      source_id: restrict to one source's passages.
      passage_author_id: restrict to edges where the passage author has this id
        (resolved via author_aliases).
      nested_author_id: restrict to edges where the cited/attributed authority
        has this id.
      stance: one of "advocates", "rejects", "discusses", "context".
      min_confidence: float 0-1. The extractor's confidence in the stance label.
      year_min, year_max: filter on nested_attribution_year (the year the cited
        authority took their position, NOT the year of the passage).
      extraction_version: "v0" (May 2026 baseline) or "v1" (current).
      include_text: if True, joins the passage text (trimmed to 400 chars).
        Default False to keep payloads small.
      limit, offset: pagination. Max limit 500.

    Returns the matching edges plus total count for the filter combination.
    """
    return _graph_query(
        "query_edges",
        claim_id=claim_id, source_id=source_id,
        passage_author_id=passage_author_id, nested_author_id=nested_author_id,
        stance=stance, min_confidence=min_confidence,
        year_min=year_min, year_max=year_max,
        extraction_version=extraction_version,
        include_text=include_text, limit=limit, offset=offset,
    )


@mcp.tool()
def author_cooccurrence(
    seed_claim_id: str | None = None,
    seed_author_id: str | None = None,
    co_with: str = "author",
    min_stance: str | None = None,
    limit: int = 20,
) -> dict:
    """Find entities that co-occur with a seed in the edge graph.

    For "who weighs in on hypothesis X", pass seed_claim_id=X co_with="author"
    and you get authors ranked by edge count, each with a stance distribution.

    For "what hypotheses does author Y engage with", pass seed_author_id=Y
    co_with="claim" and you get the claim ids they've taken positions on.

    For "what sources cite hypothesis X", pass seed_claim_id=X co_with="source"
    and you get sources ranked by edge count.

    Args:
      seed_claim_id: a claim_id to anchor the query (required if no author).
      seed_author_id: an author_id to anchor the query (required if no claim).
      co_with: what to group results by — "author" | "claim" | "source".
      min_stance: optional filter — only count edges with this stance.
      limit: max results (default 20, max 200).

    Each result row has: entity_id, edge_count (how many edges link the entity
    to the seed), distinct_passages (number of distinct passages), and stances
    (JSON breakdown {stance: count, ...} showing how the entity's edges break
    down by stance).
    """
    return _graph_query(
        "author_cooccurrence",
        seed_claim_id=seed_claim_id, seed_author_id=seed_author_id,
        co_with=co_with, min_stance=min_stance, limit=limit,
    )


@mcp.tool()
def find_convergence(
    seed_source_id: str,
    seed_paragraph_index: int,
    min_similarity: float = 0.85,
    limit: int = 10,
    cross_source_only: bool = True,
    cross_author_only: bool = False,
) -> dict:
    """Find passages with high semantic similarity to a seed passage, restricted
    to DIFFERENT sources (or different authors). Use this to surface independent
    corroborations of a claim or observation.

    Backed by pgvector cosine similarity over the 384-dim passage embeddings,
    using the HNSW index for fast lookup.

    Args:
      seed_source_id, seed_paragraph_index: the seed passage to find
        corroborations for.
      min_similarity: 0-1 cosine similarity threshold. 0.85 is restrictive
        (near-duplicate); 0.70 is loose (same topic, different angle); 0.55
        is very loose (related discussion).
      limit: max results (default 10, max 50).
      cross_source_only: if True (default), exclude same-source matches —
        guarantees the corroboration is from a different document.
      cross_author_only: if True, additionally exclude same-passage-author
        matches. Use when you want to be sure two HUMANS converged, not
        the same person across multiple posts.

    Returns up to `limit` candidates each with: source_id, paragraph_index,
    similarity, passage_year, passage_author, text_snippet (first 400 chars).
    """
    return _graph_query(
        "find_convergence",
        seed_source_id=seed_source_id, seed_paragraph_index=seed_paragraph_index,
        min_similarity=min_similarity, limit=limit,
        cross_source_only=cross_source_only, cross_author_only=cross_author_only,
    )


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


# ---------- Middleware patches: drop TrustedHost, add CORS ----------
#
# Two changes to the default Starlette middleware stack that FastMCP
# attached when we called mcp.streamable_http_app():
#
#   1. TrustedHostMiddleware is designed for localhost / stdio servers;
#      behind a public Vercel HTTPS endpoint every request comes in via a
#      cloud-fronted hostname and TrustedHost rejects them all with 421
#      "Invalid Host header". Drop it.
#
#   2. CORSMiddleware is needed because the bridge in the conversational
#      sandbox calls this MCP from a browser at https://tavus-sandbox.vercel.app.
#      Without it, the browser's same-origin policy blocks the fetch
#      before it leaves with a "Failed to fetch" (no HTTP status).
#      The mcp-session-id header in particular must be in expose_headers
#      so the browser-side bridge can read it back from responses and
#      reuse it on subsequent calls.
#
# Both changes manipulate app.user_middleware then set app.middleware_stack
# to None to force Starlette to rebuild the stack on the next request.

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

try:
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    # 1. Drop TrustedHost
    app.user_middleware = [
        m for m in app.user_middleware
        if getattr(m, "cls", None) is not TrustedHostMiddleware
    ]

    # 2. Prepend CORS so it runs first on every request, including
    #    OPTIONS preflights generated by browsers ahead of POSTs.
    #    Origins listed here are the only browsers allowed to talk to
    #    the MCP. Add new entries when new front-ends come online.
    app.user_middleware.insert(0, Middleware(
        CORSMiddleware,
        allow_origins=[
            "https://tavus-sandbox.vercel.app",
        ],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "mcp-session-id",
            "Accept",
            "Authorization",
        ],
        expose_headers=["mcp-session-id"],
        max_age=86400,
    ))

    # Force Starlette to rebuild the middleware stack with the new ordering
    app.middleware_stack = None
except Exception as _e:
    print(f"[startup] warning: could not patch middleware stack: {_e}", file=sys.stderr)




# ---------- REST shim (HTTP-callable wrappers around the MCP tools) ----------
#
# Provides plain HTTP endpoints so clients without MCP JSON-RPC support
# (ChatGPT browsing, simple curl, Lovable apps, etc.) can invoke each tool.
#
# Pattern:
#   POST /api/v1/{tool_name}        body: JSON object of named arguments
#   GET  /api/v1/tools              returns the tool index + parameter schema
#
# Empty body == call with all defaults. Unknown parameters return 400 with
# the valid parameter list so callers can self-correct.

import inspect
from starlette.routing import Route as _Route
from starlette.responses import JSONResponse as _JSONResponse


_REST_TOOLS = {
    "list_sources":         list_sources,
    "search_sources":       search_sources,
    "verify_quotation":     verify_quotation,
    "cite_passage":         cite_passage,
    "source_status":        source_status,
    "trace_claim_history":  trace_claim_history,
    "ask_research_question": ask_research_question,
    "list_claims":          list_claims,
    "query_edges":          query_edges,
    "author_cooccurrence":  author_cooccurrence,
    "find_convergence":     find_convergence,
}


async def rest_tool_invoke(request):
    tool_name = request.path_params["tool_name"]
    fn = _REST_TOOLS.get(tool_name)
    if fn is None:
        return _JSONResponse(
            {"error": f"unknown tool: {tool_name}",
             "available_tools": sorted(_REST_TOOLS.keys())},
            status_code=404,
        )

    body_bytes = await request.body()
    if body_bytes:
        try:
            kwargs = json.loads(body_bytes)
        except json.JSONDecodeError as e:
            return _JSONResponse({"error": f"invalid JSON body: {e}"}, status_code=400)
        if not isinstance(kwargs, dict):
            return _JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
    else:
        kwargs = {}

    sig = inspect.signature(fn)
    valid_params = set(sig.parameters.keys())
    unknown = set(kwargs.keys()) - valid_params
    if unknown:
        return _JSONResponse(
            {"error": f"unknown parameters: {sorted(unknown)}",
             "valid_parameters": sorted(valid_params)},
            status_code=400,
        )

    try:
        result = fn(**kwargs)
    except TypeError as e:
        return _JSONResponse({"error": f"bad arguments: {e}"}, status_code=400)
    except Exception as e:
        return _JSONResponse({"error": f"tool failed: {e}"}, status_code=500)

    return _JSONResponse(result)


async def rest_tools_list(request):
    """List all available REST-callable tools with their signatures."""
    out = {}
    for name, fn in _REST_TOOLS.items():
        sig = inspect.signature(fn)
        params = {}
        for pname, p in sig.parameters.items():
            ann = str(p.annotation).replace("typing.", "") if p.annotation != inspect.Parameter.empty else "any"
            default = None if p.default == inspect.Parameter.empty else p.default
            required = p.default == inspect.Parameter.empty
            try:
                json.dumps(default)
            except (TypeError, ValueError):
                default = str(default)
            params[pname] = {"type": ann, "default": default, "required": required}
        doc = (fn.__doc__ or "").strip()
        out[name] = {
            "summary": doc.split("\n\n")[0] if doc else "",
            "parameters": params,
            "endpoint": f"POST /api/v1/{name}",
        }
    return _JSONResponse({
        "service": "citation-verifier-mcp REST shim",
        "tool_count": len(out),
        "tools": out,
    })


app.routes.insert(0, _Route("/api/v1/tools",       rest_tools_list,  methods=["GET"]))
app.routes.insert(0, _Route("/api/v1/{tool_name}", rest_tool_invoke, methods=["POST"]))


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
        "tools_available": ["list_sources", "search_sources", "verify_quotation",
                            "cite_passage", "source_status", "trace_claim_history",
                            "ask_research_question",
                            "list_claims", "query_edges", "author_cooccurrence",
                            "find_convergence"],
        "rest_shim": {
            "tool_invoke":   "POST /api/v1/{tool_name}  (JSON body of named arguments)",
            "tool_index":    "GET  /api/v1/tools",
            "purpose":       "for clients without MCP JSON-RPC support (e.g. ChatGPT browsing)",
        },
    })


app.routes.insert(0, Route("/", health))
app.routes.insert(1, Route("/health", health))
