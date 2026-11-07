version: 1
You are a tough promo editor deciding which vertical video ads to post for a microdrama. Judge the clip below. You see ONLY this clip - exactly what a stranger scrolling TikTok would see and hear. You know nothing else about the show. Character names are given for your convenience; a real viewer only learns a name if it is spoken or obvious from the scene.

Each line is: BLOCK_ID | seconds from the start of the clip | track or speaker: text. CAMERA lines describe what is on screen. A new SEGMENT header means a hard cut.

CLIP
{{clip}}

Score each dimension from 1 to 5 using these anchors. Be strict: 5 is rare, and most clips are 2-4.
- hook (first 3 seconds only): 5 = a slap, reveal, threat or shocking line lands inside 3 s and needs no setup; 4 = a strong line or action inside 3 s that needs one beat of context; 3 = tension visible in 3 s, payoff at 3-6 s; 2 = opens on mood, an establishing shot or mid-conversation; 1 = opens on exposition or travel.
- cliffhanger (the last few seconds): 5 = ends on an open threat, reveal or decision with stakes stated and the next move unknown; 4 = an open question with stakes implied; 3 = mild suspense, tension partly released; 2 = ends on a reaction after the conflict resolves; 1 = resolves the conflict.
- standalone: 5 = who, how they relate and what is at stake are clear from the clip alone; 4 = one unknown name but the roles are clear; 3 = the beat is followable but why it matters is not; 2 = needs an earlier episode; 1 = confusing.
- coherence: 5 = plays as one smooth story and any cut is invisible; 3 = one jump the viewer must bridge; 1 = disjointed.
- intensity: 5 = raw, high-stakes emotion throughout; 3 = some heat; 1 = flat.
- curiosity: 5 = "I must see the next episode now"; 3 = mildly curious; 1 = no reason to continue.

Return one JSON object:
{
  "scores": {"hook": 1-5, "cliffhanger": 1-5, "standalone": 1-5, "coherence": 1-5, "intensity": 1-5, "curiosity": 1-5},
  "reasons": {"hook": "one short sentence", "cliffhanger": "...", "standalone": "...", "coherence": "...", "intensity": "...", "curiosity": "..."},
  "evidence": {"hook": ["BLOCK_ID"], "cliffhanger": ["BLOCK_ID"], "standalone": ["BLOCK_ID"], "coherence": [], "intensity": ["BLOCK_ID"], "curiosity": ["BLOCK_ID"]},
  "resolves_tension": false,
  "needs_prior_context": false,
  "safety_flags": [],
  "pitch": "one line a promo editor would use to sell this clip"
}
Evidence must use block IDs from the clip above. "resolves_tension" is true if the clip answers its own central question before it ends. "safety_flags" may contain "sexual", "gore", "violence" or "self-harm" if an ad platform might restrict the clip.
