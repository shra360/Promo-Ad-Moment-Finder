# Evaluation on the 50-episode pack

Status, 6 Nov 2026: the code, the answer key and the no-AI baseline are scored. The AI pipeline's scores come from
`run_eval.bat`, run in your own terminal (the AI service is not reachable from Claude's workspace). The same
tables then fill in for the AI run in `runs\ai\eval_report.md`.

## 1. Mechanical correctness (code, all 50 episodes)

| Check | Result |
| --- | --- |
| Episodes parsed | 50 of 50; 3,727 blocks, 1,487 spoken lines, 102.7 min |
| Shipped promos passing every rule V1-V11 | 100% |
| Cuts that land inside a spoken line, in any valid candidate | 0 |
| Cliffhanger blocks cut short, in any valid candidate | 0 |
| Automated tests | 28 pass, here and on your computer |

## 2. The answer key, and how far to trust it

All 50 episodes were labelled by independent Claude agents that never saw the tool's picks
(`eval/ANNOTATION_GUIDE.md`, `eval/labels/`). Each label names the best hook, the best cliffhanger, up to two
alternatives each, 1-2 must-use promos (91 in all) and a 1-5 promo-strength rating.

A second annotator relabelled 10 episodes (1, 5, 10, 15, 20, 25, 30, 35, 40, 50) to measure agreement
(`eval/agreement.py`, a match is within 3 s):

| Agreement between two annotators | Share of episodes |
| --- | --- |
| Same best hook | 0.8 |
| Best hook among the other's picks | 1.0 |
| Same best cliffhanger | 1.0 |
| Must-use promos overlap | 1.0 |
| Strength rating identical / within 1 | 0.7 / 1.0 (rank correlation 0.85) |

The key is consistent, but both annotators are Claude, so this agreement is likely higher than two people would
reach. Treat the key as a strong second opinion, not ground truth. A person spot-checking 5-10 episodes in the
app's *Label episodes* tab is the next step.

## 3. Scores: no-AI baseline against the answer key

Chance level = random clean-cut 20-60 s windows, the same number per episode, scored with the same code
(`eval/random_control.py`, mean of 20 seeds).

| Measure | Baseline | Chance |
| --- | --- | --- |
| Hook hit@3, strict / lenient | 0.32 / 0.64 | 0.15 / 0.35 |
| Cliffhanger hit@3, strict / lenient | 0.24 / 0.54 | 0.13 / 0.38 |
| Must-use promos found by a gated candidate | 0.73 | 0.56 |
| Ranked list from episodes the key rates 4-5 | 0.84 | 0.71 (all episodes: 0.58) |
| Mean key strength of the top-5 slate | 4.2 | 3.8 (all episodes: 3.62) |
| Rank correlation, key strength vs the episode's best score | 0.54 | - |

The keyword baseline is about twice chance on hooks and cliffhangers, and its ranking favours the right episodes.
It still misses the key's best hook in two episodes out of three.

## 4. Where the baseline picks badly

- **Words without meaning.** Rank 4 opens on "I don't know!": the exclamation mark and the delivery cue
  score high, but out of context the line says nothing to a stranger.
- **Strong episodes missed entirely.** In episodes 13, 27, 29, 31 and 42 (the key rates all five 4-5), none of
  the baseline's top 3 matches the key's hook or cliffhanger, even counting alternatives.
- **Endings over middles.** 4 of 25 ranked promos end in an episode's last 10 s, where the script itself is
  built to cliffhang; it rarely finds mid-episode cut points.
- **No standalone test.** It cannot tell whether a stranger would follow the clip; that score is a line-count
  guess.
- **Ad safety is a word list.** 11 of 25 ranked promos carry a brand-safety flag, mostly for words like
  "poison" or "gallows". A person decides.

These are the gaps the AI pipeline's blind judge and standalone check are meant to close; the AI run will show
whether they do.

## 5. What the AI run adds to this file

`runs\ai\eval_report.md` will hold the same answer-key table with a column for the baseline, plus the model's
self-correction rate, judge agreement between its two samples, and failure examples. After that run, the next
steps are a blind precision check (top 10 mixed with 5 controls, in the app's *Review* tab) and the new
synthetic episode sets.
