"""Test C: prove or disprove the attribution-blindness hypothesis.

From draft-claim source:
    const passageTexts: string[] = passages.map((p: any) => p.text);
    const verified = q.length > 0 && passageTexts.some((t) => t.includes(q));

This checks the quote is in SOME passage. It does NOT check that the passage
matches the source_id named in supporting_evidence.

If true, an LLM could quote Davis verbatim, cite it as Zattera, and the
gate would PASS the verification.
"""

# Simulate exactly what the gate does
def gate_verify(quote: str, passages: list[dict]) -> bool:
    """Mirror the draft-claim verification logic."""
    q = quote.strip()
    if not q:
        return False
    return any(q in p["text"] for p in passages)

# Real retrieved passages from a recent query: a Davis-quote and a Zattera-quote
passages = [
    {
        "source_id": "davis_2020_glyphs_scribes",
        "paragraph_index": 100,
        "text": "use of at least two dialects, the provenance, the codicological structure. To these we can now add the number of scribes and an understanding of the collaborative nature of its creation.",
    },
    {
        "source_id": "zattera_2022_alphabet",
        "paragraph_index": 0,
        "text": 'It is shown how the structure of Voynich words can be described by assuming each word type is composed of 12 "slots", each of which can be filled by one of a small set of glyphs or be left empty.',
    },
]

# Construct an evidence claim that quotes DAVIS but attributes it to ZATTERA
misattributed_evidence = {
    "source_id": "zattera_2022_alphabet",  # WRONG — quote is from Davis
    "passage_reference": "passage 1",
    "quoted_text": "To these we can now add the number of scribes and an understanding of the collaborative nature of its creation.",
}

correct_evidence = {
    "source_id": "davis_2020_glyphs_scribes",  # correct
    "passage_reference": "passage 1",
    "quoted_text": "To these we can now add the number of scribes and an understanding of the collaborative nature of its creation.",
}

# Run both through the gate
ver_mis = gate_verify(misattributed_evidence["quoted_text"], passages)
ver_corr = gate_verify(correct_evidence["quoted_text"], passages)

print("=" * 70)
print("TEST C — does the gate enforce source_id ↔ quote provenance?")
print("=" * 70)
print()
print(f"Quote: \"{misattributed_evidence['quoted_text'][:80]}...\"")
print()
print(f"  Correctly attributed (source_id=davis_2020):  verified={ver_corr}")
print(f"  MISATTRIBUTED (source_id=zattera_2022):       verified={ver_mis}")
print()
if ver_mis:
    print("⚠️  CONFIRMED BUG: gate passes misattribution as ✓ verified.")
    print("    The verification logic checks for the quote anywhere in the")
    print("    retrieved set, but never checks that the named source_id is")
    print("    the actual provenance of the quote.")
else:
    print("Hypothesis FALSIFIED: gate caught the misattribution.")
