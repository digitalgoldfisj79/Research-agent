# Research-agent

A citation-discipline ledger for AI-assisted research, applied to Voynich Manuscript studies as a proof of concept.

## What this is

A protocol and a tool. The protocol defines what makes an AI-assisted research claim acceptable: it must have verified citations, a specific falsifier, an explicit dependency graph, and a tracked verification status. The tool is a web application that enforces the protocol — claims cannot be published without their citations being verified against actual source text.

## Why this exists

In May 2026 I posted a fabricated quotation to the Voynich Ninja forum during a discussion. The quotation came from an AI-assisted analysis I trusted. The forum has an explicit rule against LLM-generated content, and the call-out was correct.

This repository is the discipline I am applying to my own work afterward. Every claim here has been verified against its source by a verification step that runs at submission time. Every claim states what would falsify it. Retractions propagate to dependent claims. The protocol is open. If others find it useful, they are welcome to use or fork it.

## Status

In active development. The schema is settled. The first claims from existing Voynich research are being added. The web application is being built using Lovable as the front-end builder and a Render-hosted MCP server for citation verification.

## License

MIT. See LICENSE.

## Reading order

1. `docs/PROTOCOL.md` — what the discipline is and why
2. `docs/ARCHITECTURE.md` — how the parts fit together
3. `schema/claim.schema.json` — the formal structure of a claim
4. `claims/` — the actual ledger entries
5. `corpus/` — the primary sources claims are verified against

## Not affiliated with

Voynich Ninja forum, voynich.nu, Cipher Mysteries, voynichrevisionist, or any institution. This is an independent personal project.
