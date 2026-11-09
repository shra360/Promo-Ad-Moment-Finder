# Evaluation

Status, 7 Nov 2026. Scored so far: the code's mechanics, the answer key, the no-AI baseline on the 50-episode
Kuku pack, the baseline on the 160-episode ReelBench synthetic pack, and stress tests. Not yet scored: the AI
pipeline itself, which needs your Gemini key and runs in your own terminal (`run_eval.bat`, `run_bench.bat`);
the AI service is not reachable from Claude's workspace. The full AI code path was exercised end to end on all
5 synthetic shows with a stand-in model, so the plumbing is tested; the model's judgement is not.

There is no training anywhere in this tool. Code applies fixed rules, and a pre-trained language model follows
written prompts. The weights and thresholds in `config.yaml` were set by hand, not fitted to any data.

## 1. Mechanical correctness (code)

| Check | Result |
| --- | --- |
| Kuku pack parsed | 50 of 50 episodes; 3,727 blocks, 1,487 spoken lines, 102.7 min |
| ReelBench pack parsed | 160 of 160 episodes, 0 issues |
| Screenplay format variants parsed identically (section 5) | 14 of 14 (was 10 of 14) |
| Shipped promos passing every rule V1-V11 | 100% |
| Cuts inside a spoken line, or cliffhangers cut short, in any valid candidate | 0 |
| Automated tests | 37 pass |

## 2. The answer key for the Kuku pack

Independent Claude agents labelled all 50 episodes without seeing the tool's picks (`eval/ANNOTATION_GUIDE.md`):
best hook, best cliffhanger, up to two alternatives each, 1-2 must-use promos (91 in all), and 1-5 strength.
A second annotator relabelled 10 episodes (`eval/agreement.py`, a match is within 3 s):

| Agreement between two annotators | Share of the 10 episodes |
| --- | --- |
| Same best hook / best hook among the other's picks | 0.8 / 1.0 |
| Same best cliffhanger | 1.0 |
| Must-use promos overlap | 1.0 |
| Strength identical / within 1 | 0.7 / 1.0 (rank correlation 0.85) |

Both annotators are Claude, so this agreement is likely higher than two people would reach. Treat the key as a
strong second opinion, not ground truth; a person spot-checking 5-10 episodes is the next step.

## 3. Kuku pack: no-AI baseline against the answer key

Chance = random clean-cut 20-60 s windows, as many per episode as the baseline's pool (`eval/random_control.py`).

| Measure | Baseline | Chance |
| --- | --- | --- |
| Hook hit@3, strict / lenient | 0.32 / 0.62 | 0.15 / 0.35 |
| Cliffhanger hit@3, strict / lenient | 0.24 / 0.54 | 0.13 / 0.38 |
| Must-use promos found by a gated candidate | 0.73 | 0.56 |
| Ranked list from episodes the key rates 4-5 | 0.84 | 0.71 (all episodes: 0.58) |
| Mean key strength of the top-5 slate | 4.2 | 3.8 (all episodes: 3.62) |
| Rank correlation, key strength vs the episode's best score | 0.54 | - |

## 4. ReelBench: 5 synthetic shows, 160 episodes

**What the pack really tests.** All 160 episodes are one template: the same 38 blocks at the same times, two
scenes, many identical stock lines ("Who authorized you to interfere in this matter?!"), with names and props
swapped. Every episode's ground truth is the same two windows, 39.3-64.0 s and 90.2-113.0 s. So a guess that
ignores the text scores 100%: a high score here proves little, a low score still shows a real failure. The
pack is useful for running new shows end to end, for exposing logic that over-reacts to repeated text, and as
an IoU yardstick.

**Two ground-truth conventions that differ from the brief.**
- Window 1 ends on the hero's comeback line, a payoff. The brief asks for an unresolved ending, so the AI judge
  may score these clips' cliffhangers low on purpose. Treat a miss there as a definitional difference.
- Both windows start in a 2.4-2.6 s silence before the first line. The notes on all 160 window-1 entries say
  "opening with antagonist provocation", but the provocation line ends exactly where the window starts. The
  tool's cuts start a median 3.9 s earlier and include that line, matching the notes rather than the times.
  This looks like an off-by-one-beat in the benchmark; it costs IoU, not recall@0.5.

**Baseline scores** (IoU = overlap / union within the episode; recall@0.5 = windows matched at IoU 0.5 or more):

| Candidates considered | Recall@0.5 before the fixes | After |
| --- | --- | --- |
| Any valid candidate | 0.78 | 0.76 |
| Passed the judge's gates | 0.44 | 0.72 |
| Top 3 per episode | 0.38 | 0.62 |
| Final ranked list (ceiling 0.39: 25 promos per show, about 62 windows) | 0.13 | 0.20 |
| Control: random clean cuts | 0.27 | 0.31 |
| Control: fixed-time guess, no text | 1.0 | 1.0 |

After the fixes, top-3 recall is 0.89 on window 1 but 0.34 on window 2. By category it runs from 0.86 (Betrayal
Peak) down to 0.39 (Cliffhanger Vow). Of the 90 windows still missed, 74 are window 2: its first line carries
none of the baseline's trigger words or delivery cues, so the keyword baseline rarely proposes it.

## 5. Stress tests (`eval/stress.py`)

| Test | Result |
| --- | --- |
| Shift every time by 13.7 s | 89 of 89 picks moved by exactly 13.7 s: picks depend on content, not position |
| Play scene 2 before scene 1 | Ground-truth windows covered: 318 of 320 before, 315 of 320 after: picks follow the story |
| 14 alternative spellings of the format | 10 of 14 parsed identically at first; 14 of 14 after the parser fixes |

The 4 format failures were silent: without `**bold**` labels the parser found 0 blocks; with times lacking
parentheses, with an hours field (`00:00:39.30`) or with an en dash instead of `|`, every block lost its times.
A run would have finished and found nothing. The parser now reads all four, and any episode with no usable
timings is reported as UNUSABLE and left out, so it can never be half-processed silently.

## 6. Fixes made from these tests, and their effect on both packs

| Failure found | Fix | ReelBench | Kuku pack |
| --- | --- | --- | --- |
| Duplicate check treated different episodes as the same story because they share stock lines or sound cues | Words weighted by rarity across the show; a match needs the shared words to carry real information | Gated recall 0.44 -> 0.72 | Unchanged: the same 3 genuine re-stagings still removed |
| Baseline penalised clips ending in an episode's last 10 s at the proposal stage | Penalty removed: proposals favour recall; variety is handled in ranking | Window-2 pool recall 0.34 -> 0.54 | Lenient hook hit 0.64 -> 0.62, all else unchanged |
| Silent parse failures on 4 format variants | Tolerant patterns; UNUSABLE episodes reported and skipped | 14 of 14 variants | Parses identically to before |

## 7. Where the logic is still likely to fail, and how to reduce the error

1. **Moments with no surface cues** (window 2 here). The keyword baseline cannot read meaning. This is the AI
   miner's job; `run_bench.bat` measures whether it closes the gap. If it does not, show the miner the
   episode's last 25 s separately, since end-of-episode turns are where this misses.
2. **Payoff vs cliffhanger.** The brief rewards unresolved endings; ReelBench and many real editors also post
   payoff clips. Decide which Kuku wants and set it in the judge prompt, or rank both kinds in separate lists.
3. **List capacity.** 25 promos per show cannot cover 2 per episode. Raise `final_count`, or keep the shortlist
   and treat the gated pool as the editor's longer list.
4. **Hook lead-in.** The tool requires the hook within 1 s of the cut; the brief allows 3 s. A 2-3 s visual
   lead-in (a door bursting open) is legitimate. `hook_window` in `config.yaml` controls this.
5. **Name drift across episodes** (64 speaker labels in the Kuku pack). Handled by the AI's series bible, which
   is not yet tested on a real model; spot-check `bible.json` after the first AI run.
6. **Templated or formulaic shows.** Fixed here for duplicates, but any rule that compares wording should use the
   rarity weighting in `rank.py`.
7. **The answer key itself.** Same model family as a Claude-run pipeline would be; use Gemini for the AI run,
   and add your own labels for 5-10 episodes in the app.

## 8. Next

Run `run_eval.bat` (Kuku pack) and `run_bench.bat` (ReelBench, 8 episodes per show) with your Gemini key. Each
writes the same tables for the AI pipeline next to the baseline: `runs\ai\eval_report.md` and
`bench\ai_sub8\reelbench_report.md`.
