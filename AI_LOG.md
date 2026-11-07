# AI use log

What each AI tool did, and the mistakes caught along the way. Source material for the solution doc's "where AI got it wrong" section.

## Tools

| Tool | Used for |
| --- | --- |
| Claude (claude.ai, with code tools) | Planning, dataset audit, writing the pipeline, tests and app |
| Pipeline model (configurable; default Gemini Flash) | Series bible, promo mining, montage, blind judging |

## Mistakes caught

| # | What went wrong | How it was caught | Fix |
| --- | --- | --- | --- |
| 1 | Claude's helper agents could not open the project's screenplay files, so the first plan (copy files in parallel) produced nothing | Every helper reported it had no access | Did ingestion in the main session; later worked from the original zip |
| 2 | Claude retyped episode 50 by hand for a parser test; the copy differed from the original (an extra trailing newline) | Byte comparison with the original after the zip arrived | Original files only from then on |
| 3 | The first parser assumed `Scene start:` in mixed case; episode 6 writes `SCENE START:`, so it parsed as 0 s long | Full 50-episode audit showed episode 6 with duration 0 | Header keys matched case-insensitively |
| 4 | The first dedupe rule treated a montage that reused 8 s of another promo's footage as a duplicate, which removed every cross-episode montage | Mock run on all 50 showed 0 montages surviving | Overlap measured against the whole promo's length |
| 5 | Keyword baseline ranks "I don't know!" as a top hook because of its delivery cue and exclamation mark, though it means nothing out of context | Reading the baseline's report | Kept as a known baseline failure; the model judge is meant to catch it |

### Caught by an independent AI code review (a second Claude agent that had not seen the code being written)

| # | What went wrong in Claude's code | Evidence | Fix (with a regression test) |
| --- | --- | --- | --- |
| 6 | An end cut could snap backwards past the cliffhanger line, so the line never played, yet validation passed | Ep 28 promo ended at 48.2 s; its cliffhanger line runs 48.2-49.7 s | Ends only move later; the last segment covers the whole cliffhanger block; V11 requires it to play in full |
| 7 | One malformed model answer (a list, "4/5" as a score, null aliases) crashed the whole run, and the cache replayed the crash | Reproduced with crafted answers | Every answer is type-checked; unusable judge samples are dropped; each task's failure is isolated |
| 8 | Retry rules matched the word "rate" inside "generateContent", so a Gemini 404 was retried 7 times; timeouts were never retried | Simulated errors | Retries follow the HTTP status; timeouts and dropped connections retry |
| 9 | If every model call failed (e.g. a wrong key), the run "succeeded" with 0 promos | Simulated 401s | The run stops with the first errors; the manifest lists every failure |
| 10 | Switching provider in the app kept Gemini's key and endpoint | Code reading | Config reloads for the chosen provider |
| 11 | The montage index only ever saw episodes 1-30 | Simulation | Round-robin across episodes |
| 12 | hit@3 counted labels from episodes not in the run | Adding a label for episode 70 halved the score | Per-label denominators over in-run episodes |
| 13 | Cache writes were not atomic; Mac `._` files and BOMs broke loading; an empty episode filter crashed | Reproduced | Atomic writes, junk files skipped, BOM-safe reads, clear errors |

### Evaluation setup

| # | What went wrong | How it was caught | Fix |
| --- | --- | --- | --- |
| 14 | The key check retried an unreachable API for minutes before failing | It hung in the cloud workspace, which cannot reach Google | The check now uses one retry and a 30 s timeout; it fails in about 5 s with the reason |
| 15 | Neither Claude's cloud workspace nor the Linux shell on the user's computer can reach Google's API (network allowlist) | `curl` to generativelanguage.googleapis.com failed from both | The Gemini run happens in the user's own Windows terminal via `run_eval.bat` |

The answer key in `eval/labels/` was written by Claude agents, not by a person. Where it matters, it is the
same model family as a Claude-run pipeline would be, so scores against it would flatter that pipeline; the
Gemini run avoids this.

Add model mistakes from real runs here: `llm_calls.jsonl` records every call that needed a correction round, and `candidates.json` lists proposals code could not build (for example, invented block IDs).
