# Private OpenRouter MCP

This Vercel function exposes two MCP tools:

- `list_openrouter_models` — discover current OpenRouter model IDs and metadata.
- `openrouter_chat` — send a bounded, non-streaming chat-completions request to a named OpenRouter model.

## Production endpoint

```text
https://research-agent-mcp-edwardbozzard-3935s-projects.vercel.app/openrouter-mcp?token=<OPENROUTER_MCP_ACCESS_TOKEN>
```

Health check:

```text
https://research-agent-mcp-edwardbozzard-3935s-projects.vercel.app/openrouter-health
```

## Required Vercel configuration

In the `research-agent-mcp` Vercel project:

1. Under **Settings → Deployment Protection**, disable **Vercel Authentication** for production deployments, or restrict it to preview deployments. External MCP clients cannot traverse Vercel's interactive login page.
2. Under **Settings → Environment Variables**, add these as sensitive variables for Production:
   - `OPENROUTER_API_KEY` — the OpenRouter API key.
   - `OPENROUTER_MCP_ACCESS_TOKEN` — a separate long random token used only to protect this MCP endpoint.
3. Redeploy the latest production deployment so the variables are loaded.
4. Confirm `/openrouter-health` reports both configuration flags as `true`.

Optional metadata variables:

- `OPENROUTER_APP_TITLE`
- `OPENROUTER_APP_URL`

## ChatGPT developer-mode connection

Add the complete production endpoint, including the `token` query parameter, as the remote MCP URL in ChatGPT developer mode.

The query-token mechanism is an interim private-use control. Query strings can appear in service logs. Do not reuse the OpenRouter API key as the access token. Implement OAuth 2.1 before sharing or publishing this MCP server.
