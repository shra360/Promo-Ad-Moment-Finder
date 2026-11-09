"""Write outputs/Top3_windows_per_episode_50_episodes.csv and .md from a sensational run.
Usage: python report/export_top3.py [runs/sensational_keyword]"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
run = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs" / "sensational_keyword"
r = json.loads((run / "sensational.json").read_text(encoding="utf-8"))
out = ROOT / "outputs"
out.mkdir(exist_ok=True)
rows = []
for n, v in sorted(r["per_episode"].items(), key=lambda x: int(x[0])):
    for i, w in enumerate(v, 1):
        rows.append([int(n), i, w["start_ts"], w["end_ts"], f"{w['duration']:.1f}", f"{w['score']:.2f}", w["mean_heat"],
                     w["peak"], "; ".join(w["tags"]), w["opens_on"], w["ends_on"]])
hdr = ["Episode", "Rank in episode", "Start (MM:SS.ff)", "End (MM:SS.ff)", "Duration (s)", "Score", "Mean heat /10",
       "Peak heat /10", "Heated moments", "Opens on", "Ends on"]
with open(out / "Top3_windows_per_episode_50_episodes.csv", "w", newline="", encoding="utf-8-sig") as fh:
    csv.writer(fh).writerows([hdr] + rows)
L = ["# Top 3 sensational windows in each of the 50 episodes", "",
     f"Show: I Married a Broken Soldier. He was a Duke. Grader: {r['grader']}"
     + (f"; final assessment: {r['final_assessment']}" if r.get("final_assessment") else "")
     + ". Every window is 20-60 s and starts and ends where nobody is mid-sentence. Times are MM:SS.ff from the "
       "start of the episode.", "",
     "| Ep | # | Start | End | Duration | Score | Heated moments | Opens on |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
for x in rows:
    L.append(f"| {x[0]} | {x[1]} | {x[2]} | {x[3]} | {x[4]} s | {x[5]} | {x[8] or '-'} | {x[9][:70].replace('|', '/')} |")
(out / "Top3_windows_per_episode_50_episodes.md").write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"{len(rows)} windows -> {out}")
