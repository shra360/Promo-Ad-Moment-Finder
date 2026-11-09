# Promo Finder

Finds the **top 3 most sensational 20-60 second promo windows** in a microdrama's screenplays: Gemini grades every
moment for heat (emotion, conflict, shock, slaps and other physical action, humiliation, open questions); code
turns the grades into a heat curve and picks the hottest windows, cut only where nobody is mid-sentence.
Built for the Kuku AI Generalist assignment, Problem 1. The 2-page report is `report/Promo_Finder_Report.pdf`.

## Start

```
pip install -r requirements.txt
python app.py
```

`python app.py` opens the app in your browser (or double-click `start_app.bat` on Windows). Pick the grader, the
episodes (the 50 Kuku episodes in `data/raw` are preselected) and press **Find the top windows**. Without a key,
choose the keyword grader.

Command line: `python -m promo_finder sensational --input data/raw --out runs/my_run` (add `--mode baseline` for
the keyword grader). Outputs: `sensational.md` (top 3 with start, end and duration), `sensational_top.csv`,
`sensational_per_episode.csv` (top 3 in every episode), `block_grades.json`.

The earlier full pipeline (hook / cliffhanger / blind judge / ranking) is still here: `python -m promo_finder run ...`
and `streamlit run app_promo_pipeline.py`. The rest of this file describes it.


Finds the best 20-60 second promos in a microdrama's screenplays, with exact cut points, and ranks them.
Built for the Kuku AI Generalist assignment, Problem 1 ("Find the Promos").

A good promo opens on a hook in the first 3 seconds, ends on a cliffhanger, and makes sense to someone who has never seen the show. For every promo the tool gives the episode and start and end time of each segment, the hook, the cliffhanger, why it works, scores, and any reason to check it before posting.

**The model picks moments; code sets every timestamp.** The model only names screenplay blocks (`E021-S01-B004`). Code looks up their times, snaps each cut to a point where nobody is mid-sentence, checks lengths and rules, and ranks.

## Where the API key goes

Only one place is needed. Pick one:

| Option | What to do |
| --- | --- |
| `.env` file (recommended) | Create a file named `.env` in this folder (next to `config.yaml`) with one line: `GEMINI_API_KEY=your-key`. `.env.example` shows the format. `.env` is git-ignored, so it never reaches GitHub |
| Paste when asked | `run_eval.bat` and `run_bench.bat` ask for the key if there is no `.env`; it is kept for that window only |
| Environment variable | `set GEMINI_API_KEY=your-key` in the same terminal before running |
| Streamlit app | Paste it in the sidebar's *API key* box, or on Streamlit Cloud add `GEMINI_API_KEY` under the app's *Secrets* |

Other providers use the same `.env` file: `LLM_PROVIDER=anthropic` plus `ANTHROPIC_API_KEY=...`, or
`LLM_PROVIDER=openai_compat` plus `LLM_BASE_URL=...`, `LLM_API_KEY=...` and `LLM_MODEL=...`.
`python -m promo_finder ping` checks the key with one tiny call. The key is never written to any output file
(the run manifest records only whether one was set).

## Quick start (Windows)

1. Open **Anaconda Prompt** (Python 3.10 or newer) and go to this folder:
   `cd "%USERPROFILE%\Desktop\Promo Moment Detector for Microdramas"`
2. Get a free Gemini API key from Google AI Studio. Either save it in a file named `.env` in this folder as
   `GEMINI_API_KEY=your-key` (see `.env.example`), or paste it when the script asks.
3. Run `run_eval.bat test` first (episodes 1-3, a few minutes), then `run_eval.bat` for all 50.

The script installs the requirements, checks the key with one tiny call, runs the no-AI baseline, then the AI
pipeline, and scores both against the answer key in `eval/`. Results land in `runs\ai\`: start with `report.md`
(promos to post first) and `eval_report.md` (how good they are, and where the tool picks badly).
If the free tier's daily quota runs out, the run stops with a message; run the same command later and it
continues from where it stopped (every finished model call is cached in `.cache\`).

Browse, review and label results in the app: `streamlit run app.py`.

## Command line (any OS)

```bash
pip install -r requirements.txt
python -m promo_finder ping                                   # check model + key
python -m promo_finder run --input data/raw --out runs/baseline --mode baseline --reference eval/reference.json
python -m promo_finder run --input data/raw --out runs/ai --baseline runs/baseline --reference eval/reference.json
python -m promo_finder run --input path/to/new_show.zip --out runs/new_show --episodes 1-10
```

The input can be a folder or a `.zip` of episode files.

## Switching models

No code change. Set environment variables (or edit `config.yaml`, or use the app's sidebar):

| Provider | Variables |
| --- | --- |
| Google Gemini (default) | `GEMINI_API_KEY`, optional `LLM_MODEL=gemini-3.1-pro-preview` |
| Anthropic | `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, `LLM_MODEL=claude-sonnet-5-5` |
| Any OpenAI-compatible API (Groq, OpenRouter, Ollama, a Kuku endpoint) | `LLM_PROVIDER=openai_compat`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| Offline test | `LLM_PROVIDER=mock` (uses the baseline's logic through the full AI path) |

`LLM_JUDGE_MODEL` sets a different model for the judge. A different model family for judging than for finding reduces the risk of a model favouring its own picks.

**Quality first.** Use the strongest model you have for the judge. Keep `judge_samples: 2` so each clip is scored twice and disagreements are reported. Lower `max_workers` if the free tier rate-limits you. Every model call is cached in `.cache/llm/`, so an interrupted run resumes where it stopped and a rerun costs nothing.

**Size of a full run on the 50-episode pack:** about 450 model calls, roughly 0.75M input and 0.2M output tokens with the default settings (most of it is the judge: about 200 valid clips, scored twice).

## How it works

| Stage | Done by | Output |
| --- | --- | --- |
| 0. Parse every screenplay into timed blocks; audit the structure | code | `parsed/`, `audit.csv` |
| 1. Series bible: who each speaker label is, per episode (labels drift: 64 labels in the pack) | model, 1 call | `bible.json` |
| 2. Mine each episode for promo proposals, as block IDs | model, 1 call per episode | `proposals.json` |
| 3. Build exact segments: snap to clean cuts; jump-cut over-long proposals | code | `candidates.json` |
| 4. Validate rules V1-V11 (IDs exist, clean cuts, 20-60 s, hook in the first second, order, no overlap) | code | `validation.json` |
| 5. Cross-episode montages from the valid moments | model, 1 call | `montage.json` |
| 6. Blind judge: scores each clip seeing only the clip, on a 1-5 rubric with anchors | model, 2 samples per clip | `judgements.json` |
| 7. Rank: gates (hook, cliffhanger, standalone at least 3), weighted score, dedupe by story beat, per-episode cap, top-5 slate | code | `promos_ranked.json`, `.csv`, `report.md` |
| 8. Evaluate: funnel, rejection reasons, judge agreement, bias checks, failure examples; your labels and reviews if present | code | `eval.json`, `eval_report.md` |

When a model answer breaks a rule code can check (an unknown block ID, wrong length, a hook that is not at the start), the model is shown the problems once and asked to fix them. Both answers are logged in `llm_calls.jsonl`.

## Checking quality

**Answer key.** `eval/labels/` holds an answer key for all 50 episodes: per episode, the best hook block,
the best cliffhanger block, up to two alternatives each, 1-2 must-use promos, and a 1-5 promo-strength rating.
Ten independent Claude agents wrote it, following `eval/ANNOTATION_GUIDE.md`, without seeing any of the tool's
picks. `eval/check_label.py` checks every file; `eval/build_reference.py` merges them into `eval/reference.json`.
It is an AI answer key, not a human one: spot-check a few episodes yourself in the app's *Label episodes* tab.

**Measures** (in `eval_report.md`, for any run given `--reference`):

| Measure | Meaning |
| --- | --- |
| Hook / cliffhanger hit@3 | One of the episode's top 3 candidates puts its hook (cliffhanger) within 3 s of the key's. "Lenient" also accepts the key's alternatives |
| Must-use promos found | Share of the key's must-use promos covered at least half by a candidate that passed the gates |
| Ranked from strong episodes | Share of the ranked list from episodes the key rates 4-5 |
| Rank correlation | Spearman correlation between the key's episode strength and the episode's best score |

**Chance level.** `python eval/random_control.py runs/<run> eval/reference.json` scores random clean-cut
windows the same way, so every number has a floor to compare against.

**Your reviews.** The app's *Review* tab mixes the top 10 with 5 valid candidates that did not make the list,
shuffled and unscored; your approve/reject calls give precision@10 against a blind control.

## ReelBench (synthetic pack) and stress tests

`data/reelbench/` holds a synthetic pack of 5 shows, 160 episodes, with ground-truth promo windows.
`run_bench.bat` runs the no-AI baseline and the AI pipeline on it (8 episodes per show by default, `all` for
every episode) and scores both by IoU against the ground truth, next to two controls: a fixed-time guess and
random cuts. `python eval/stress.py --bench data/reelbench --real data/raw` checks the parser against 14
alternative spellings of the screenplay format, and checks that picks follow the story when it moves in time.
Results and what they mean: `EVALUATION.md`.

## New episode sets

Put the files in a new folder (or a zip) and run with `--input` pointing at it and a new `--out`. Nothing in the
code is specific to this show. To score a new set, it needs its own answer key: write `eval/labels`-style files
(by hand, by the annotation guide, or from the ground truth of a synthetic set) and build a `reference.json`
for it with `python eval/build_reference.py <labels_dir> <episodes_dir> <out.json>`.

## Tests

```bash
python -m pytest -q
```

Requires Python 3.10 or newer.

The tests use invented screenplays (`tests/fixtures.py`); no Kuku text is in the repo.

## Confidentiality

The input pack and every run folder contain screenplay text. `data/`, `runs/`, `.cache/` and `reference.json` are git-ignored. Share run folders only through a private link.

## Limitations

- It reads scripts, not video, so it cannot judge acting, faces, music or pacing.
- Script timings are assumed to match the final video.
- "Best" is a judgement; with no view data it is not a prediction of performance.
- A model grades a model's picks; your labels are the outside check.
- Built and tuned on one show; other genres may need different weights.
