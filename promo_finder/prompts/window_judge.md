version: 1
You are a senior promo editor for vertical microdrama ads (TikTok, Reels, Shorts). Below are {{count}} candidate
clips from episode {{n}} (logline: {{logline}}), found by a heat score. Grade each clip as a whole ad.

A great clip: something in the first 3 seconds stops a scrolling thumb (a slap, a reveal, a threat, a shocking
line); the heat keeps up (emotion, conflict, physical action, humiliation); it ends on an unresolved moment; and
a stranger can follow who is against whom.

Each line: BLOCK_ID | seconds from the clip start | track or speaker: text.
{{windows}}

For every clip return a grade from 0 to 10 (0 = unusable, 5 = decent, 8 = strong, 10 = the best ad this show
could have). You may tighten a clip by naming the block it should start on and the block it should end on,
both from inside that clip, keeping it at least 20 seconds long; otherwise repeat its first and last block.

Return one JSON object:
{"windows": [{"id": "W1", "score": 0-10, "start_block": "BLOCK_ID", "end_block": "BLOCK_ID",
              "why": "at most 15 words"}]}
