"""Re-run Test C against the patched verification algorithm."""

def gate_verify_patched(quote: str, source_id: str, passages: list[dict]) -> dict:
    """Mirror the v2 verification logic."""
    q = quote.strip()
    if not q:
        return {"verified": False, "verification_status": "no_quote_provided"}
    
    named = [p["text"] for p in passages if p["source_id"] == source_id]
    if any(q in t for t in named):
        return {"verified": True, "verification_status": "verbatim_in_named_source"}
    
    all_texts = [p["text"] for p in passages]
    if any(q in t for t in all_texts):
        return {"verified": False, "verification_status": "verbatim_but_wrong_source"}
    
    return {"verified": False, "verification_status": "not_in_corpus"}

passages = [
    {"source_id": "davis_2020_glyphs_scribes",
     "text": "use of at least two dialects, the provenance, the codicological structure. To these we can now add the number of scribes and an understanding of the collaborative nature of its creation."},
    {"source_id": "zattera_2022_alphabet",
     "text": 'It is shown how the structure of Voynich words can be described by assuming each word type is composed of 12 "slots", each of which can be filled by one of a small set of glyphs or be left empty.'},
]

real_quote = "To these we can now add the number of scribes and an understanding of the collaborative nature of its creation."

cases = [
    ("correct attribution",      real_quote, "davis_2020_glyphs_scribes"),
    ("misattributed to Zattera", real_quote, "zattera_2022_alphabet"),
    ("invented quote",            "The Voynich Manuscript is written in Old Norse.", "davis_2020_glyphs_scribes"),
    ("empty quote (paraphrase)",  "", "davis_2020_glyphs_scribes"),
]

print(f"{'case':<30} {'verified':<10} {'status':<32}")
print("-" * 75)
for label, q, sid in cases:
    r = gate_verify_patched(q, sid, passages)
    v = "✓" if r["verified"] else "✗"
    print(f"  {label:<28} {v} {str(r['verified']):<8} {r['verification_status']}")

print()
print("Expected:")
print("  correct attribution        → ✓ verbatim_in_named_source")
print("  misattributed to Zattera   → ✗ verbatim_but_wrong_source  (was the bug)")
print("  invented quote             → ✗ not_in_corpus")
print("  empty quote                → ✗ no_quote_provided")
