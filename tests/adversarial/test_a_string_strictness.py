"""Test A: simulate the gate's verification on real passages with controlled modifications.

Pulls a known-verified quote, applies modifications, runs through the same
includes() check the gate uses.
"""

# Pull a real, recently-verified quote from earlier tests
# This is the verbatim quote from Zattera p61 that we saw verified ✓
real_quote = 'It is shown how the structure of Voynich words can be described by assuming each word type is composed of 12 "slots"'

# We need a passage text that DOES contain it — for an honest test, fetch it
# from Supabase. Actually let me just simulate; the include() check is the
# same regardless of which passage it's run against.
passage_with_quote = (
    'This paper presents a new transliteration alphabet for Voynichese. '
    'It is shown how the structure of Voynich words can be described by '
    'assuming each word type is composed of 12 "slots", each of which can '
    'be filled by one of a small set of glyphs or be left empty.'
)

# Confirm the baseline match works
assert real_quote in passage_with_quote, "Baseline match failed — bad fixture"

# Generate modified variants
import unicodedata

modifications = {
    "baseline_exact": real_quote,
    "single_typo_char_swap":           real_quote.replace("structure", "stucture"),
    "extra_space_inside":              real_quote.replace("Voynich words", "Voynich  words"),
    "missing_period_at_end":           real_quote.rstrip(".").rstrip(),
    "smart_double_quote_open":         real_quote.replace('"slots"', '\u201cslots"'),
    "smart_double_quote_both":         real_quote.replace('"slots"', '\u201cslots\u201d'),
    "em_dash_for_hyphen_NA":           real_quote,  # quote has no hyphen, baseline
    "lowercase_first_word":            real_quote[0].lower() + real_quote[1:],
    "trailing_whitespace":             real_quote + " ",
    "unicode_NBSP_for_space":          real_quote.replace("the structure", "the\u00a0structure"),
    "remove_one_char_middle":          real_quote.replace('structure', 'structur'),
    "case_change_one_word":            real_quote.replace("Voynich", "voynich"),
}

print(f"{'modification':<40} verify?")
print("-" * 55)
for name, variant in modifications.items():
    verified = variant in passage_with_quote
    flag = "✓ PASS" if verified else "✗ REJECT"
    print(f"  {name:<38} {flag}")

print()
print("Interpretation:")
print("  - Every modification ABOVE that shows ✓ PASS means the gate would")
print("    accept an LLM-introduced typo / smart-quote / NBSP / case change.")
print("  - REJECT means strict-match catches the modification.")
