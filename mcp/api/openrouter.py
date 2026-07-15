"""Private OpenRouter MCP adapter deployed as a Vercel ASGI function.

Secrets are read only from Vercel environment variables:
  OPENROUTER_API_KEY            required for model calls
  OPENROUTER_MCP_ACCESS_TOKEN   required to access this MCP endpoint

Connect clients to:
  https://<deployment>/openrouter-mcp?token=<OPENROUTER_MCP_ACCESS_TOKEN>

The query-token gate is intended for private developer-mode use. Replace it
with standards-compliant OAuth 2.1 before wider distribution.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ACCESS_TOKEN = os.environ.get("OPENROUTER_MCP_ACCESS_TOKEN", "").strip()
APP_TITLE = os.environ.get("OPENROUTER_APP_TITLE", "Private OpenRouter MCP")
APP_URL = os.environ.get("OPENROUTER_APP_URL", "")

_http: httpx.Client | None = None


def _client() -> httpx.Client:
    global _http
    if _http is None:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": APP_TITLE,
        }
        if APP_URL:
            headers["HTTP-Referer"] = APP_URL
        _http = httpx.Client(
            base_url=OPENROUTER_BASE_URL,
            headers=headers,
            timeout=90.0,
        )
    return _http


def _configured() -> tuple[bool, str | None]:
    if not ACCESS_TOKEN:
        return False, "OPENROUTER_MCP_ACCESS_TOKEN is not configured"
    if not OPENROUTER_API_KEY:
        return False, "OPENROUTER_API_KEY is not configured"
    return True, None


mcp = FastMCP(
    "openrouter-private",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


@mcp.tool()
def list_openrouter_models(query: str = "", limit: int = 25) -> dict[str, Any]:
    """Use this when you need to discover OpenRouter model IDs before making a call.

    Optionally filter by text found in a model's ID, name, description, or provider.
    Returns a compact model catalogue including context length, pricing, and supported
    parameters. This is read-only and does not generate model usage charges.
    """
    ok, error = _configured()
    if not ok:
        return {"error": error}

    limit = max(1, min(int(limit), 100))
    needle = (query or "").strip().lower()
    try:
        response = _client().get("/models")
        response.raise_for_status()
        models = response.json().get("data", [])
    except httpx.HTTPStatusError as exc:
        return {
            "error": "OpenRouter models request failed",
            "status_code": exc.response.status_code,
            "detail": exc.response.text[:1000],
        }
    except Exception as exc:
        return {"error": f"OpenRouter models request failed: {exc}"}

    if needle:
        def matches(model: dict[str, Any]) -> bool:
            haystack = " ".join(
                str(model.get(field, ""))
                for field in ("id", "name", "description", "canonical_slug")
            ).lower()
            return needle in haystack

        models = [model for model in models if matches(model)]

    compact = []
    for model in models[:limit]:
        compact.append({
            "id": model.get("id"),
            "name": model.get("name"),
            "context_length": model.get("context_length"),
            "pricing": model.get("pricing"),
            "supported_parameters": model.get("supported_parameters"),
            "architecture": model.get("architecture"),
        })
    return {"count": len(compact), "models": compact}


@mcp.tool()
def openrouter_chat(
    model: str,
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Use this when the user explicitly wants a named OpenRouter model to answer a prompt.

    The model must be an OpenRouter model ID such as `anthropic/claude-sonnet-4.6`.
    The call is non-streaming. It returns the generated text, actual routed model,
    finish reason, token usage, and reported cost when OpenRouter supplies it.
    """
    ok, error = _configured()
    if not ok:
        return {"error": error}

    model = (model or "").strip()
    prompt = (prompt or "").strip()
    if not model:
        return {"error": "model is required"}
    if not prompt:
        return {"error": "prompt is required"}

    max_tokens = max(1, min(int(max_tokens), 32768))
    temperature = max(0.0, min(float(temperature), 2.0))
    messages: list[dict[str, str]] = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }

    try:
        response = _client().post("/chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        return {
            "error": "OpenRouter completion failed",
            "status_code": exc.response.status_code,
            "detail": exc.response.text[:2000],
        }
    except Exception as exc:
        return {"error": f"OpenRouter completion failed: {exc}"}

    choices = body.get("choices") or []
    first = choices[0] if choices else {}
    message = first.get("message") or {}
    return {
        "id": body.get("id"),
        "requested_model": model,
        "model": body.get("model"),
        "content": message.get("content"),
        "finish_reason": first.get("finish_reason"),
        "native_finish_reason": first.get("native_finish_reason"),
        "usage": body.get("usage"),
    }


app = mcp.streamable_http_app()


class PrivateAccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Health endpoints reveal configuration state but never secret values.
        if request.url.path in ("/openrouter-health", "/health", "/"):
            return await call_next(request)

        if not ACCESS_TOKEN:
            return JSONResponse(
                {"error": "server access token is not configured"},
                status_code=503,
            )

        supplied = request.query_params.get("token", "")
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            supplied = auth[7:].strip()
        if supplied != ACCESS_TOKEN:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


app.add_middleware(PrivateAccessMiddleware)


async def health(request):
    return JSONResponse({
        "service": "openrouter-private-mcp",
        "status": (
            "ok"
            if OPENROUTER_API_KEY and ACCESS_TOKEN
            else "configuration_required"
        ),
        "mcp_endpoint": "/openrouter-mcp?token=<OPENROUTER_MCP_ACCESS_TOKEN>",
        "openrouter_key_configured": bool(OPENROUTER_API_KEY),
        "access_token_configured": bool(ACCESS_TOKEN),
        "auth": "query token or bearer token (developer-mode only)",
        "tools_available": ["list_openrouter_models", "openrouter_chat"],
    })


app.routes.insert(0, Route("/", health, methods=["GET"]))
app.routes.insert(1, Route("/health", health, methods=["GET"]))
app.routes.insert(2, Route("/openrouter-health", health, methods=["GET"]))
