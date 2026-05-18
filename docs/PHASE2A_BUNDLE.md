# Phase 2A — Safety hygiene bundle

Date: 2026-05-17

This bundle brings the GitHub repo current with what's deployed to Supabase
and adds operational hygiene. Skipped per user direction: OpenRouter key
rotation, manual Supabase ops.

## Contents

| path | purpose |
|---|---|
| `supabase/functions/upsert-source/index.ts` | new — thin endpoint used by the ninja crawler to upsert a source row before passages, gated by `x-ingest-secret` |
| `scripts/ninja_crawler.py` | the sequential voynich.ninja crawler, MyBB archive view, per-post chunking, 30-word filter, blockquote stripping |
| `scripts/ninja_crawler_parallel.py` | 3-worker parallel version of above — ~10× faster |
| `scripts/voynich_ninja_thread_index.json` | enumeration of all 2,200 research-grade forum threads (forum-id → thread-ids) |
| `tests/adversarial/adversarial_tests.md` | full results of the 6-test adversarial suite (5 pass, 1 bug-found-and-fixed) |
| `tests/adversarial/test_a_string_strictness.py` | Test A: gate is character-strict |
| `tests/adversarial/test_c_attribution.py` | Test C: demonstrates the pre-fix attribution bug |
| `tests/adversarial/test_c_post_fix.py` | Test C: re-run against patched logic, all 4 cases correct |
| `.github/workflows/heartbeat.yml` | daily cron ping to `/heartbeat`, prevents Supabase 7-day idle pause |

## Deploying the heartbeat workflow

After committing, in GitHub repo settings → Secrets and variables → Actions:
- Add secret `SUPABASE_PROJECT_REF` = `ymaqlcfjmdwncdbjprmw`

That's all. The workflow runs daily and on manual dispatch.

## Live state at time of bundle

| metric | value |
|---|---|
| Supabase DB size | ~175 MB / 500 MB |
| Total embedded passages | ~38,500 |
| Forum threads ingested | 1,921 of 2,084 (Theories tail still outstanding) |
| Academic papers | 7 |
| voynich.nu pages | 134 |
| Wikipedia | 1 |
| Edge functions deployed | 9 (ingest-passages, search-passages, draft-claim, heartbeat, add-source, ingest-transliteration, get-sources, get-passage, upsert-source) |
| draft-claim version | v2 (source-id-aware verification, Test C fix) |
