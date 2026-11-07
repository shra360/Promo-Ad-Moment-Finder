version: 1
You are building a character bible for a vertical microdrama series, so that later steps know who is who.

Speaker labels in these screenplays are unreliable. The same person can appear under a name in one episode and under a description (for example "WOMAN IN RED") in another, and one descriptive label can mean different people in different episodes. Decide, episode by episode, who each label is, using the logline, the continuity notes, the camera line before the label first speaks, and the label's sample lines.

SERIES: {{title}}

EPISODES
{{episodes}}

Return one JSON object:
{
  "premise": "2-3 plain sentences a stranger could follow: who the lead is, what happened to her, what she is fighting for",
  "characters": [
    {"name": "canonical name", "who": "one line: role and key relationships", "labels": ["every speaker label used for this character"]}
  ],
  "aliases": {
    "<episode number>": {"<SPEAKER LABEL exactly as written>": "<canonical name, or a short role for an unnamed extra, e.g. 'a royal guard'>"}
  },
  "uncertain": ["<episode>:<LABEL> - why the mapping is unsure"]
}

Rules:
- Map every label listed for every episode.
- Use one canonical spelling per character (the most frequent one).
- Do not invent characters or relationships the material does not support.
- Put any low-confidence mapping in "uncertain" as well as in "aliases".
