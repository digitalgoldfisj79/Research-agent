# Architecture

The system has four components. None of them is novel individually; the combination is the contribution.

## The four components

### 1. The web application

Built using Lovable. Serves the read-only ledger view to anyone with the URL. Authenticated contributors can submit new claims through a structured form. Backend runs on Supabase (Postgres database, auth, server-side functions).

User-facing routes:
- `/` — homepage, list of recent claims
- `/claims` — full ledger with filters and search
- `/claims/:id` — single claim detail page with full evidence chain, dependency graph, and discussion notes
- `/submit` — new claim form (authenticated)
- `/protocol` — the PROTOCOL.md content rendered as a page
- `/about` — what the project is, why it exists, who maintains it

### 2. The model gateway

OpenRouter. One API endpoint, one API key, access to any LLM. The application calls OpenRouter for:
- The semantic verification step at claim submission (asks a model whether the cited source supports the claim)
- Optionally: claim drafting assistance for users who have a research question but not yet a structured claim

Model selection is configuration. The default for verification is a cheap-and-fast model (Gemini Flash tier or equivalent). Critical-path verification can be configured to use a higher-quality model (GPT-5.5 or Claude Opus) when justified.

The application does not call OpenAI, Anthropic, or Google directly. All model calls go through OpenRouter so the architecture survives model deprecation, provider changes, and pricing shifts.

### 3. The citation verifier

A separate small service that hosts the corpus and provides one critical primitive: given a quoted passage and a source ID, does the passage actually appear in the source?

This is implemented as a Model Context Protocol (MCP) server, hosted on Render. The application calls it during submission to verify every citation before accepting the claim.

The MCP exposes:
- `list_sources()` — what's in the corpus
- `search_sources(query)` — find passages matching query terms
- `verify_quotation(text, source_id)` — does this text appear in this source
- `cite_passage(source_id, paragraph_index)` — fetch canonical text plus citation string

The corpus is held as plain text files with provenance metadata (URL, fetch date, SHA-256). Updates to the corpus are tracked: if a source page changes, the new version is added with a new fetched-at timestamp and the old one is marked as superseded but retained for historical citation integrity.

### 4. The ledger storage

Two parallel stores, kept in sync:

- Supabase Postgres for the live application (fast reads, transactional writes, integration with the web app)
- Git repository in this GitHub repo (canonical, public, forkable, version-controlled)

On every claim submission or retraction, the application writes to Postgres and also pushes a JSON file change to the Git repo. The Git repo is the durable record; Postgres is the operational store. If the application disappears, the Git repo is still the ledger.

## Data flow at submission time

```
User fills submission form in web app
     │
     ▼
Web app server-side: validate structure
     │
     ▼
For each citation, call citation MCP's verify_quotation
     │
     ├── If any citation fails → reject submission, show error
     ▼
For each citation, call OpenRouter (Gemini Flash) for semantic check
     │
     ├── If model says "source does not support claim" → status=flagged
     │
     ▼
For each dependency, verify the referenced claim exists and is not retracted
     │
     ├── If any dependency invalid → reject submission
     ▼
Validate falsifier is non-empty and non-boilerplate
     │
     ▼
Write claim to Supabase Postgres
     │
     ▼
Generate JSON file for claim, push commit to Git repo
     │
     ▼
Return success to user with the new claim's URL
```

## Data flow at retraction time

```
Author or contributor submits retraction with reason
     │
     ▼
Application identifies all claims with this claim in depends_on
     │
     ▼
For each downstream claim, set status=flagged, log retraction in flag reason
     │
     ▼
Notify the authors of flagged downstream claims
     │
     ▼
Update Postgres, push commit to Git repo
```

## Why this architecture

**Separation of concerns.** The web app does presentation and basic data flow. The MCP does corpus management and citation verification. The model gateway abstracts the LLM provider. The Git repo is the durable record. Each piece can be replaced independently.

**Substrate-independent of any single tool.** Lovable might disappear or become unsuitable. Supabase might raise prices. OpenRouter might change terms. Render might shut down. Each of these is replaceable without rewriting the others. The protocol survives any single substrate failure.

**Forkable from day one.** The Git repo is the source of truth. Anyone can fork this repository, point a new application at their fork, and run their own instance with their own corpus and their own community. The protocol is open. The implementation is open.

**The discipline is enforced server-side.** A user could bypass the web app, get an API key, and try to submit a claim with a fabricated citation. The verifier still runs. The protocol still applies. The discipline is not in the user's hands.

## What's not in this architecture

- No autonomous agent dialogue. Submissions are human-initiated, AI-assisted, then human-reviewed.
- No automated scheduled discovery. The system processes what's submitted; it does not proactively generate hypotheses or poll for new content.
- No social features. No comments, no votes, no leaderboards. Only structured claims, citations, dependencies, and retractions.
- No exclusivity. Anyone with the URL can read. Anyone with a contributor account (granted by the maintainers) can submit. The application does not gate participation by reputation, role, or institutional affiliation.

These omissions are deliberate. The bet is that a small, focused tool that does one thing well is more valuable than a comprehensive platform that does many things adequately.
