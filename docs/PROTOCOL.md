# The Protocol

This document specifies what makes a claim acceptable for the ledger. The protocol is the load-bearing artefact of this project; the application enforces it but does not define it.

## Definitions

**Claim.** A specific, falsifiable statement about the Voynich Manuscript or about scholarship on the manuscript. Not a question, not a hypothesis without falsifier, not an interpretation without grounding.

**Source.** A primary or secondary document held in the corpus. Sources are immutable once registered: each source has a SHA-256 hash and a fetched-at timestamp, and any subsequent change to the source produces a new source entry rather than overwriting the old one.

**Citation.** A specific passage in a specific source, used as evidence for a specific claim. Citations are verified by an automated check that the cited passage actually appears in the source. A claim cannot be published with unverified citations.

**Falsifier.** A specific empirical condition that would, if true, require the claim to be retracted or substantially revised. "I would change my mind if I saw X" is the form. Claims without a specific falsifier are rhetorical, not analytical, and the ledger does not accept them.

**Dependency.** A claim that the current claim builds on. If claim B depends on claim A, then retraction of A automatically flags B for re-verification. The dependency graph is visible to all readers.

**Status.** A claim is in one of these states:
- `draft` — submitted, not yet validated
- `verified` — citations verified, falsifier specified, ready for community scrutiny
- `flagged` — automated semantic check raised concern; human review needed
- `contested` — another claim or human reviewer has surfaced a substantive disagreement
- `retracted` — withdrawn by author or contradicted by subsequent evidence

## Submission rules

A submitted claim must include:

1. `claim_text` — what is being asserted, in one to three sentences
2. `supporting_evidence` — at least one citation, each pointing to a source in the corpus and a specific passage
3. `falsifier` — what would falsify or substantially weaken the claim
4. `depends_on` — list of existing ledger claim IDs that this claim builds on (may be empty)
5. `category` — a broad topical tag (zodiac, herbal, paleography, codicology, calendar, cipher, etc.)
6. `submitted_by` — the author's identifier

At submission time, the application:

1. Verifies every citation by checking that the quoted text appears in the named source. Failure on any citation rejects the submission with an error naming the failing citation.
2. Checks that the falsifier is non-empty and not boilerplate. A minimum length and a check against a list of empty-form patterns ("I would update my views if new evidence emerged") is applied.
3. Confirms that every listed dependency exists in the ledger and is not itself retracted.
4. Optionally, runs a semantic check via an LLM: given the claim and the cited passage, does the source actually support what the claim attributes to it? This is advisory rather than blocking — semantic disagreements set status to `flagged` rather than rejecting.

## Retraction rules

A claim can be retracted by:

1. The original author, with a stated reason
2. Any contributor with a citation showing the claim is contradicted by evidence

Retraction is permanent. Retracted claims remain in the ledger with `retracted: true` and a `retraction_reason`. Downstream claims (those that depended on the retracted claim) are automatically set to `flagged` and the author is notified.

## What this protocol does not catch

Stated honestly so readers know the limits:

- Citation discipline catches fabrication. It does not catch misinterpretation of correctly-cited sources.
- The semantic check catches gross mismatches between claim and source. It does not catch subtle drift, especially across multi-step inferences.
- The dependency graph prevents citation laundering through the ledger. It does not prevent laundering through external venues (forum posts, blog entries, conversations).
- The falsifier requirement forces claims to be testable in principle. It does not guarantee they will be tested in practice.
- The ledger is curated, not refereed. Editorial judgement about what gets accepted lives with the maintainers.

This protocol reduces specific failure modes. It does not produce correctness. Human scrutiny remains the final check.

## Why this matters

AI-assisted research produces confident-sounding output faster than humans can verify it. Without structural discipline, the failure mode is silent fabrication of citations, plausible-but-unsupported inferences, and gradual drift in derived claims as they propagate through subsequent work. Each of these failure modes is well-documented in published literature on retrieval-augmented generation systems.

The protocol here is one response: make the discipline structural, applied at submission time, with retraction propagation visible. The bet is that visible discipline applied consistently is more useful than a perfect framework that exists only in theory.

## Forking

If you maintain a research community in a different domain, you can fork this repository and apply the protocol to your own corpus. The Voynich-specific parts are the corpus contents, the category tags, and a handful of domain references in the source code. Everything else is general. If you fork it, please leave the LICENSE and credit the origin. No other obligations.
