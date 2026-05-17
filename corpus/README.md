# Corpus

Primary sources that claims in this ledger are verified against.

## Structure

Each source is a plain text file with a header containing:
- `Title:` — descriptive title
- `Source URL:` — where it came from (if web-accessible)
- `Source Citation:` — full bibliographic reference for print sources
- `Fetched:` — ISO 8601 timestamp of ingest
- `SHA256:` — hash of the file body for drift detection
- `License:` — usage terms

Followed by a blank line and the source text.

## Source IDs

Source IDs are stable, filesystem-safe identifiers used in claim citations. The convention:
- Web sources: `{host_with_underscores}__{path_slug}` (e.g., `voynich_nu__transcr`)
- Print/scholarly works: `{author_lastname}_{year}_{short_descriptor}` (e.g., `davis_2020_scribes`)
- Manuscript references: `{library_abbrev}_{shelfmark}` (e.g., `bl_cotton_julius_a_vi`)

## Permissions and licensing

The corpus uses primary sources under fair use for academic citation and verification. Where sources are explicitly licensed, the licence is noted in the file header. Where sources are copyrighted but used with permission, the permission is documented in `PERMISSIONS.md`.

Sources whose use is fair-use citation rather than redistribution:
- Excerpts from Cipher Mysteries (Pelling) — to be requested if substantial use is needed
- Excerpts from voynichrevisionist (O'Donovan) — to be requested if substantial use is needed
- Voynich Ninja forum posts — only with explicit per-post consent

Sources currently used with explicit consent or as openly available:
- voynich.nu — Zandbergen has indicated consumption-with-citation is acceptable
- Academic papers in this directory are excerpts under fair use for citation
- Manuscript references are to public-domain medieval works

## Drift detection

When the citation verifier is re-run periodically, it re-fetches web-sourced material and compares SHA-256 hashes. Changed sources are marked with `drift_detected_at` and the citation verifier flags any claim citing them for re-verification.

## Adding to the corpus

To add a source:
1. Fetch or transcribe the text
2. Create the file with the standard header
3. Commit to this directory
4. The citation verifier will pick it up on its next reload

Corpus additions should be conservative. The signal-to-noise ratio of the verifier depends on the corpus being authoritative, not exhaustive.
