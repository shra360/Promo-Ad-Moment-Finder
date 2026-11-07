# Annotation guide: promo answer key

You are an experienced promo editor at a microdrama app (think ReelShort or DramaBox). For each episode you are given, mark the moments YOU would use to cut a 20-60 second vertical ad that pulls in viewers who have never seen the show. Work only from the episode screenplay. You have not seen, and must not look for, any tool's picks: do not open anything under `runs/`, `.cache/`, `promo_finder/`, or other agents' label files.

## What makes a good promo (the client's brief)

1. **Hook** - something in the first 3 seconds that makes a scrolling viewer stop: a slap, a reveal, a threat, a shocking line, a public humiliation, a violent or startling action. Not an establishing shot, small talk, or exposition.
2. **Cliffhanger** - the clip cuts at an unresolved moment: a threat just made, a reveal just landing, a question hanging, a decision not yet taken. It ends BEFORE the tension resolves.
3. **Stands alone** - a stranger can tell who is against whom and what is at stake from the clip itself.

## The episode files

`eval/views/episode-NNN.txt`. Each line is `BLOCK_ID | start-end | TRACK or SPEAKER: text`. CAMERA lines describe the shot; ACTION and SOUND FX lines describe what happens; other lines are dialogue with the script's speaker label and delivery. Tracks overlap in time. Times are MM:SS.ff from the episode start.

## What to record per episode

Write one JSON file per episode to `eval/labels/episode-NNN.json` (three-digit number):

```json
{
  "episode": 7,
  "best_hook": {"block": "E007-S01-B012", "why": "one line"},
  "alt_hooks": ["E007-S02-B031"],
  "best_cliffhanger": {"block": "E007-S02-B058", "why": "one line"},
  "alt_cliffhangers": ["E007-S01-B040"],
  "must_promos": [
    {"start_block": "E007-S01-B012", "end_block": "E007-S01-B040",
     "hook_block": "E007-S01-B012", "cliffhanger_block": "E007-S01-B040",
     "why": "one line on why an editor would cut exactly this"}
  ],
  "promo_strength": 4,
  "standalone_risk": "what a stranger would not understand from this episode's best moment",
  "notes": ""
}
```

Rules:
- `best_hook`: the single best block for the first 3 seconds of an ad cut from this episode. Pick a dialogue, ACTION or SOUND FX block, not a CAMERA block.
- `alt_hooks`: 0-2 other blocks that would be nearly as good. Leave empty if nothing else is close.
- `best_cliffhanger`: the single best block to END an ad on. Dialogue, ACTION or SOUND FX. It does not have to be the episode's last line; a strong mid-episode cut point counts.
- `alt_cliffhangers`: 0-2 others, same rule.
- `must_promos`: 1 or 2 promos you would definitely cut from this episode. Each is ONE continuous span from `start_block`'s start time to `end_block`'s end time, and must be 20-60 seconds long. `hook_block` must be the first or nearly first block (starting within 1 s of the span start); `cliffhanger_block` must sit at the end of the span. If the episode has nothing worth promoting, give the least bad one and set `promo_strength` to 1.
- `promo_strength`: 1-5, how strong this episode is as promo material compared with typical microdrama episodes. 5 = an obvious ad (a slap, a betrayal reveal, a public humiliation, a deadly threat); 3 = usable; 1 = transitional, quiet or exposition-heavy.
- Judge each episode on its own, but use the logline and continuity notes to understand the story.
- Block IDs must be copied exactly from the file.

## Check your work

Run `python3 eval/check_label.py eval/labels/episode-NNN.json` for each file you write. Fix anything it reports, and run it again until it prints OK.
