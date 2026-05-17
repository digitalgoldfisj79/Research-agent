# Citation Verifier MCP

A Model Context Protocol server that verifies quotations against a corpus of source documents. Hosted on Vercel as a serverless function.

This is the verification backend for [Research-agent](https://github.com/digitalgoldfisj79/Research-agent). When a researcher submits a claim with cited quotations, the application calls this service to confirm that each quoted passage actually appears in the named source.

## Endpoint .,

After Vercel deploy, the live endpoint is at `https://<project>.vercel.app/mcp`. A health check is available at `/health`.

## Tools exposed

The server exposes five MCP tools:

- **list_sources** — enumerate the corpus, returning source IDs, types, and previews
- **search_sources** — find paragraphs across all sources matching given query terms
- **verify_quotation** — given a quoted text and a source ID, confirm the quote appears in the source. Returns exact match, fuzzy match with similarity score, or no match.
- **cite_passage** — fetch a specific paragraph plus a formatted citation string
- **source_status** — return metadata for a source (fetch time, SHA-256, drift detection)

## How it's wired

The Lovable frontend at the Research-agent app calls this MCP during claim submission. For each piece of `supporting_evidence` with a `quoted_text` field, the frontend calls `verify_quotation`. If the result is `verified: false`, the claim cannot be submitted with that quoted text as-is — the submitter must either supply the closest_match wording or reframe the citation.

This is the architectural primitive that prevents fabricated citations from entering the ledger.

## Corpus

The `corpus/` directory currently contains three paraphrased excerpts from Voynich scholarship, included for prototype testing. These are NOT authoritative source texts and should be replaced with permissioned ingest of the real sources before the system is used for any consequential research.

For each source in the corpus, the verifier checks every claim citation that names that `source_id`. New sources are added by dropping plain text files into `corpus/` with a header block listing Title, Author, Year, Source. Vercel auto-redeploys on each push.

## Vercel configuration

The Vercel project's root directory should be set to `mcp/` (this directory). The `vercel.json` in this directory tells Vercel to build `api/mcp.py` as a Python serverless function and route `/mcp`, `/health`, and `/` to it.

## Authentication

By default the endpoint is open. To require authentication, set the environment variable `CITATION_MCP_API_KEY` in Vercel's project settings. The server will then require `Authorization: Bearer <key>` on every request to `/mcp`. The `/health` endpoint remains open regardless.

For the personal-prototype phase, leave it open. Add the API key gate before opening submissions to other contributors.

## Local development

Install dependencies and run locally with `vercel dev`. Note that the Python runtime emulation in `vercel dev` is imperfect; for full fidelity, deploy to a Vercel preview branch instead.

## License

MIT. Same as the parent Research-agent repo.
