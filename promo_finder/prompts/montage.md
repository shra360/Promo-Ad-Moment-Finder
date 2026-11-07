version: 1
You are a senior promo editor for a microdrama app. Below is an index of strong moments already found inside single episodes of one series. Build {{n}} promos that each join moments from DIFFERENT episodes into one 20-60 second ad that a stranger can follow.

Good joins: the setup of a betrayal in an early episode, then the moment it explodes later; a shocking cold open from a later episode, then how it started; a vow made, then the vow tested. Bad joins: anything that needs an explanation between the cuts, or that changes who the viewer is rooting for.

SERIES PREMISE (the viewer does not have this)
{{premise}}

MOMENTS
Each moment lists spans you may use, as start_block..end_block with their length in seconds.
{{index}}

Rules:
- 2 or 3 segments, from at least 2 different episodes, played in the order you list them.
- Each segment is one span copied from the index: {"start_block": "...", "end_block": "..."}.
- Total length 20-60 seconds, adding up the span lengths shown.
- The first segment must open on a hook; hook_block is its first block.
- The last segment must end unresolved; cliffhanger_block is its last decisive block.
- If a later episode plays before an earlier one, set "cold_open": true.

Return one JSON object:
{"promos": [
  {"title": "...", "beat": "...", "segments": [{"start_block": "...", "end_block": "..."}], "cold_open": false,
   "hook_block": "...", "cliffhanger_block": "...", "hook": "...", "cliffhanger": "...",
   "standalone_context": "...", "why": "..."}
]}
