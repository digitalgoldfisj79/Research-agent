# Contributing

This project accepts contributions in three forms: new claims, retractions, and corpus additions.

Before contributing, read `docs/PROTOCOL.md`. The protocol is the load-bearing artefact; contributions that don't conform to it will be rejected by the application itself.

## Submitting a new claim

1. Develop your claim in your own working environment, with whatever AI assistance you find useful. The protocol applies to what gets submitted, not how you arrived at it.
2. Identify the supporting evidence: which sources, which passages.
3. Specify a falsifier that names a concrete empirical condition.
4. Identify any existing claims this claim depends on.
5. Submit via the web application or open a pull request adding a JSON file to `claims/` that conforms to `schema/claim.schema.json`.

The application will verify all citations against the corpus. If any verification fails, the submission is rejected with an error naming the failing citation. Fix the citation and resubmit.

## Proposing a retraction

If you have evidence that contradicts an existing claim, you can propose retraction. The retraction request needs:
- The claim ID to be retracted
- A clear statement of the reason
- A citation showing the contradicting evidence (verified against the corpus by the same process as a new claim)

The original author of the claim is notified. If they agree, the claim is marked retracted. If they disagree, the claim is marked contested and discussion continues until consensus or until a third party adjudicates.

Downstream claims (those that depended on the retracted claim) are automatically flagged for re-verification.

## Adding to the corpus

Corpus additions should be conservative. Adding a source means future claims can cite it. Adding a source you don't have permission to redistribute, or that is contested as authoritative, creates problems downstream.

Check `corpus/README.md` for the structure and licensing posture. Open an issue first if you're unsure whether a source should be added.

## What contributions are not

This project is not the Voynich Ninja forum. Conversational engagement happens there. The ledger is for structured claims with verified provenance. If you want to discuss a hypothesis informally, the forum is the right venue. If you want to publish a verified, falsifiable, citation-grounded claim, this is the right venue.

This project is not a venue for AI-generated content posted as if it were human. If you used AI assistance to develop a claim, the protocol's verification step is what makes that acceptable — your AI work is auditable, the citations are real, the claim is falsifiable. The application enforces these conditions automatically.

## Maintainer review

Pull requests to the claims directory are reviewed for protocol compliance. Pull requests to the application code, corpus, or documentation are reviewed for technical quality and consistency. Maintainers are not gatekeepers of claim content beyond protocol compliance; the dependency-graph and retraction-propagation mechanisms are how claim quality is policed over time.

## Code of conduct

Be precise. Cite specifically. Disagree about claims, not about contributors. Retraction is normal and expected; a retracted claim is not a personal failure. The point of the ledger is to make epistemic state visible, not to score points.
