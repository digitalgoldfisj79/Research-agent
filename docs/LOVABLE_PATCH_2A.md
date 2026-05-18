# Lovable patch — /draft page enhancements

Paste this into Lovable chat for the voynich-whispers-archive project:

---

> **Update the /draft page candidate cards with two enhancements:**
>
> **1. Source-tier badges next to each evidence item.**
>
> Each `supporting_evidence` item has a `source_id`. Map the source_id prefix to a tier badge that displays alongside the existing source attribution:
>
> - Source_id matches `davis_2020_glyphs_scribes`, `davis_2022_paleography`, `gaskell_bowern_2022_gibberish`, `bowern_gaskell_2022_enciphered`, `lindemann_2022_mattr`, `zattera_2022_alphabet`, or `farrugia_layfield_vanderplas_2022_scribes` → blue rounded badge labelled "peer-reviewed"
> - Source_id starts with `www_voynich_nu_` → grey rounded badge labelled "reference site"
> - Source_id starts with `voynich_ninja_` → amber rounded badge labelled "forum"
> - Source_id starts with `user_upload_` → purple rounded badge labelled "user-supplied" (forward-compat; this prefix doesn't exist yet but will)
> - Anything else → no badge
>
> The badge should sit immediately after the source_id in the existing monospace attribution line, e.g.:
>
>     [zattera_2022_alphabet]  [peer-reviewed]  passage 3
>
> Small, unobtrusive, sits inline.
>
> **2. Surface the new `verification_status` field with richer states.**
>
> The API now returns `verification_status` on each evidence item with one of four values:
>
> - `verbatim_in_named_source` — current ✓ "verbatim verified" green badge (unchanged)
> - `verbatim_but_wrong_source` — display ✗ in **red** with label **"verbatim but mis-attributed"**. This is the citation-gate-caught case where the LLM quoted a real passage but named the wrong source.
> - `not_in_corpus` — display ✗ in red with label **"not in corpus — possible fabrication"**
> - `no_quote_provided` — display ⊘ in grey with label **"paraphrase only — no verbatim quote"**
>
> Keep the existing `verified` boolean check as a fallback; render the richer status only when `verification_status` is present in the response.
>
> Both changes are visual-only. No API contract changes. No new fields need to be sent in requests.

---

## Why this matters

- **Tier badges** make the source-quality differential immediately legible. Currently a Zattera peer-reviewed claim and a random forum post look identical in the card. With badges, the eye can immediately filter "what does the scholarly literature say" from "what's the forum discussion."
- **Verification status states** surface the citation gate's actual judgement instead of compressing everything to a binary ✓/✗. The new `verbatim_but_wrong_source` state in particular is the *bug-catch state* — it's how the gate would flag an LLM attempting misattribution. Right now that state would just collapse to a generic ✗ which under-communicates what's happening.

## Test queries after deployment

The following should produce visibly tiered evidence in the cards:

1. **"What does Zattera's slot-based transliteration alphabet reveal about Voynich word structure?"** — should show blue "peer-reviewed" badges on the Zattera citations
2. **"What patterns have been observed in gallows characters in Voynichese?"** — mixed badges: amber "forum" on the dashstofsk/oshfdk quotes, blue "peer-reviewed" on the Davis 2022 bench-gallows quote
3. **"What has René Zandbergen argued about C14 dating?"** — amber "forum" badges (Zandbergen posts on the forum more than he writes papers about this specific point)

If you see a green ✓ next to *every* evidence item, the citation gate is doing its job. If you ever see a red ✗ with "verbatim but mis-attributed" — surface it to me. That's the bug-catch state firing in production, would be the first observed instance.
