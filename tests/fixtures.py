"""Synthetic screenplays in the pack's format. Invented text only: the real
input pack is confidential and must never be committed."""
from __future__ import annotations

from pathlib import Path

LINES = [
    ("MARA (Cold, Furious)", '"You lied to me about the will!"'),
    ("THE TALL STRANGER (Calm)", '"I lied to protect you."'),
    ("MARA (Shocked)", '"Protect me? You sold the house!"'),
    ("OLD MAN (O.S.) (Weak)", '"Mara... come here."'),
    ("MARA (Desperate, Pleading)", '"Grandfather, tell me this is not true."'),
    ("THE TALL STRANGER (Threatening)", '"Sign the papers tonight, or you lose everything."'),
    ("MARA (Defiant)", '"I would rather burn it down."'),
    ("OLD MAN (Whispering, O.S.)", '"Then the secret dies with me."'),
    ("MARA (Horrified)", '"What secret? Who is my real father?"'),
]


def ts(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def episode_text(n: int, upper_keys: bool = False, length: float = 72.0) -> str:
    k = (lambda s: s.upper()) if upper_keys else (lambda s: s)
    out = [f"EPISODE NUMBER: Episode {n}",
           f"EPISODE LOGLINE: Mara learns her inheritance was sold (test episode {n}).",
           f"CONTINUITY NOTES: Picks up directly from Episode {n - 1}'s cliffhanger." if n > 1 else
           "CONTINUITY NOTES: First episode.",
           "", "--- SCRIPT START ---", "", "SCENE 01",
           f"{k('Scene heading')}: INT. STUDY – NIGHT", f"{k('Scene start')}: 00:00.00",
           f"{k('Scene end')}: {ts(length)}", "", f"{k('Actions and dialogue')}:", ""]
    t, i = 0.0, 0
    while t < length - 0.01:
        shot = min(4.0, length - t)
        out += ["**CAMERA:**", f"Start: ({ts(t)}) | End: ({ts(t + shot)})", f"Shot {i + 1} of the study.", ""]
        if i % 3 == 1:
            out += ["**ACTION:**", f"Start: ({ts(t + 0.2)}) | End: ({ts(t + 1.5)})", "Mara slaps the stranger.", ""]
        if i % 2 == 0 and t + 5.5 <= length:
            # this line starts mid-shot and runs past the next shot change
            sp, line = LINES[(i // 2) % len(LINES)]
            out += [f"**{sp}:**", f"Start: ({ts(t + 1.0)}) | End: ({ts(t + 5.0)})", line, ""]
        t += shot
        i += 1
    out += ["**SOUND FX:**", f"Start: ({ts(length - 1.5)}) | End: ({ts(length)})",
            "Strings swell to a cliffhanger, then cut to black.", "", "--- SCRIPT END ---"]
    return "\n".join(out)


def write_pack(folder: Path, n: int = 3) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for e in range(1, n + 1):
        (folder / f"episode-{e:03d}-Episode {1000000 + e}.txt").write_text(episode_text(e, upper_keys=(e == 2)))
    return folder
