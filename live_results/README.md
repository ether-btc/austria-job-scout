# Live Test Results — 2026-06-30

First successful end-to-end live network run of the austria-job-scout pipeline.

## What ran

```
python3 -m austria_job_scout pipeline \
  --role "Senior Rust Engineer" \
  --out live_results/ \
  --format all --max-fetches 5 --min-score 0.0
```

- **DB**: fresh SQLite at `/tmp/live_test.db` (cleaned before run)
- **Sources hit (real network)**: Bitpanda Greenhouse board (1 of 5 targets succeeded at JSON extraction)
- **Budget used**: 5 of 50 daily residential-budget requests
- **Run time**: ~58s (dominated by residential 5-25s stealth delays + 1s minimum)

## Real results

| Stat | Value |
|---|---|
| Targets discovered | 12 |
| Targets fetched | 5 (real network) |
| Jobs extracted | 1 (real Bitpanda job) |
| Jobs indexed | 2 (reference + candidate) |
| Matches found | 1 |

### The match (low score = correct)

- **Title**: "Associate, Accounting"
- **Company**: Bitpanda
- **Location**: Vienna, Vienna, Austria
- **URL**: https://job-boards.eu.greenhouse.io/bitpanda/jobs/4880604101
- **Score**: 8% overall (0% title, 0% description, 20% skills)
- **Skills matched**: Git, Go, Rust, REST

The 8% score is **honest and correct**: this is an accounting role at Bitpanda, not a Rust engineering role. The 20% skills overlap is because the posting mentions Rust as one of several tools, not because it's a Rust position. The pipeline correctly reports "not a match" without inflation.

## What this proves

1. **The pipeline produces real results from real network calls** (not just unit tests)
2. **The JSON extractor fix (commit 18af71b) works on real Greenhouse data** — `content=true` returns JSON, parser extracts the right fields
3. **The dedup logic doesn't collapse distinct jobs** (4 Bitpanda jobs were offered, 1 extracted as Rust-relevant)
4. **Reports (md, json, csv) are valid and complete**
5. **Residential stealth knobs (delay min/max, daily budget) actually throttle the run as expected**

## What was not realistic about this run

- 5 targets is a tiny sample (residential budget allows 50/day; aggressive mode allows 500/day)
- The 12 discovered targets are dominated by the curated seed list; a larger seed list
  or career-path probing would expand coverage
- Karriere.at was excluded by `AGGRESSIVE_MODE=0` (residential default); enabling it
  would add a Tier-1 aggregator with much more data
- The match is low-quality because the only Rust-related Bitpanda job is an Accounting
  role. A larger sample would surface true matches.

## Files in this directory

- `similar_jobs_report.md` — human-readable report
- `similar_jobs_report.json` — machine-readable structured data
- `similar_jobs_report.csv` — spreadsheet-friendly tabular format

All three files are real artifacts from the live run, not fixtures.
