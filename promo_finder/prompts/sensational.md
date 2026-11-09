version: 1
You grade how scroll-stopping each moment of a vertical microdrama episode is, for short-form video ads
(TikTok, Reels, Shorts). Judge each block by the moment it shows, as a viewer scrolling past would feel it.

High scores: slaps and other physical violence, shocking reveals, public humiliation, betrayal, raw emotion
(screaming, sobbing, fury), threats, power reversals, forbidden romance, unanswered questions.
Low scores: establishing shots, small talk, exposition, travel, calm reflection.
For CAMERA lines, what is shown counts, not the technique: "close-up on her face" is about 0;
"snap zoom on the bloody dagger" is high. Delivery notes such as (Furious) or (Whispering, menacing) are evidence.

Anchors for "score" (0-10): 0 = nothing happens; 3 = mild tension; 5 = a clear confrontation or emotional
beat; 7 = a slap, a cruel humiliation, a major reveal or a deadly threat; 9-10 = the most shocking moment a
viewer could see in the whole show.
Sub-scores (0-3 each): emotion, conflict, shock, physical, humiliation, open_loop.
Tags: short words for heated moments, from: slap, punch, shove, grab, weapon, blood, scream, sobbing, kiss,
threat, reveal, betrayal, humiliation, power reversal, collapse, arrest, cliffhanger.

EPISODE {{n}} (logline: {{logline}})
Each line is: BLOCK_ID | start-end | track or speaker: text
{{episode}}

Return one JSON object. List every block that scores 1 or more; blocks you leave out count as 0.
{"blocks": [
  {"block": "BLOCK_ID", "score": 0-10, "emotion": 0-3, "conflict": 0-3, "shock": 0-3, "physical": 0-3,
   "humiliation": 0-3, "open_loop": 0-3, "tags": ["slap"], "why": "at most 10 words"}
]}
Copy block IDs exactly as written above.
