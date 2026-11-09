"""Promo Finder: the top 3 most sensational 20-60 s windows in a microdrama.

Run:  python app.py        (starts the app in your browser; same as `streamlit run app.py`)
"""
from __future__ import annotations

import os
import sys


def _inside_streamlit() -> bool:
    try:
        from streamlit.runtime import exists
        return exists()
    except Exception:
        return False


if __name__ == "__main__" and not _inside_streamlit():
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", os.path.abspath(__file__), *sys.argv[1:]]))

import io
import json
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from promo_finder.config import load_config
from promo_finder.parse import parse_episode
from promo_finder.pipeline import load_input, run_sensational
from promo_finder.render import block_line, sorted_blocks
from promo_finder.sensational import STEP, heat_curve

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
RUNS.mkdir(exist_ok=True)

st.set_page_config(page_title="Promo Finder", layout="wide")
st.title("Promo Finder: most sensational moments")
st.caption("Every moment of every episode is graded for heat (emotion, conflict, shock, slaps and other physical "
           "action, humiliation, open questions). Code then finds the hottest windows of 20-60 s, cut only where "
           "nobody is mid-sentence, and returns the top 3.")
go = st.button("Find the top windows", type="primary")

cfg = load_config()
has_key = bool(cfg["llm"].get("api_key"))

with st.sidebar:
    st.header("1. Episodes")
    uploads = st.file_uploader("A .zip or several .txt screenplays", type=["zip", "txt"], accept_multiple_files=True)
    local = ROOT / "data" / "raw"
    use_local = bool(local.exists() and not uploads and st.checkbox("Use data/raw (the 50 Kuku episodes)", value=True))
    episodes_filter = st.text_input("Episodes (blank = all; e.g. 1-10)", value="")
    st.header("2. Windows")
    top_n = st.slider("How many windows", 1, 10, int(cfg["pipeline"].get("top_windows", 3)))
    ideal = st.slider("Preferred length (s)", 20, 60, int(cfg["pipeline"].get("ideal_length", 40)))
    one_per_ep = st.checkbox("At most one window per episode", value=True)


def save_uploads(files) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="upload_"))
    for f in files:
        if f.name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(f.getvalue())) as z:
                for m in z.namelist():
                    rel = Path(m)
                    if rel.suffix.lower() == ".txt" and not rel.name.startswith(".") and "__MACOSX" not in rel.parts \
                            and not rel.is_absolute() and ".." not in rel.parts:
                        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                        (tmp / rel).write_bytes(z.read(m))
        else:
            (tmp / Path(f.name).name).write_bytes(f.getvalue())
    return tmp


if go:
    src = save_uploads(uploads) if uploads else (local if use_local else None)
    if not src:
        st.error("Upload screenplays, or tick 'Use data/raw'.")
        st.stop()
    run_cfg = load_config()
    mode = "llm" if run_cfg["llm"].get("api_key") else "baseline"
    run_cfg["pipeline"].update({"top_windows": top_n, "ideal_length": float(ideal),
                                "top_max_per_episode": 1 if one_per_ep else None})
    if episodes_filter:
        run_cfg["episode_filter"] = episodes_filter
    name = datetime.now().strftime("%Y%m%d-%H%M%S") + ""
    bar = st.progress(0.0, text="starting")
    try:
        run_sensational(src, RUNS / name, run_cfg, mode=mode,
                        progress=lambda m, f=None: bar.progress(min(max(f, 0.0), 1.0), text=m) if f is not None else None)
        (RUNS / name / "input_path.txt").write_text(str(src), encoding="utf-8")
        st.session_state["run"] = name
    except Exception as e:
        (RUNS / name).mkdir(parents=True, exist_ok=True)
        (RUNS / name / "error.txt").write_text(repr(e), encoding="utf-8")
        st.error(f"Run failed (details in runs/{name}/error.txt). Please try again in a while.")
        st.stop()

runs = sorted([p.name for p in RUNS.iterdir() if (p / "sensational.json").exists()], reverse=True)
if not runs:
    st.info("Press **Find the top windows** at the top. ")
    st.stop()
pick = st.selectbox("Run", runs, format_func=lambda r: r[:15], index=runs.index(st.session_state["run"]) if st.session_state.get("run") in runs else 0)
run_dir = RUNS / pick
res = json.loads((run_dir / "sensational.json").read_text(encoding="utf-8"))
grades = json.loads((run_dir / "block_grades.json").read_text(encoding="utf-8"))
src_file = run_dir / "input_path.txt"
src = Path(src_file.read_text(encoding="utf-8")) if src_file.exists() else ROOT / "data" / "raw"


@st.cache_data(show_spinner=False)
def episodes_of(path: str):
    try:
        return load_input(path)[0]
    except Exception:
        return {}


eps = episodes_of(str(src))
st.caption(f"{res['episodes']} episodes · {res['windows_scored']:,} windows of 20-60 s scored")

st.subheader(f"Top {len(res['top'])} windows")
for w in res["top"]:
    with st.container(border=True):
        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown(f"### #{w['rank']} · Episode {w['episode']} · {w['start_ts']} – {w['end_ts']} · "
                        f"**{w['duration']:.1f} s**")
            st.markdown(f"**Score {w['score']:.2f}** (mean heat {w['mean_heat']}/10, peak {w['peak']}/10, "
                        f"first 3 s {w['hook']}/10, last 5 s {w['ending']}/10)")
            if w["tags"]:
                st.markdown("Heated moments: " + " · ".join(f"`{t}`" for t in w["tags"]))
            st.markdown(f"**Opens on:** {w['opens_on']}  \n**Ends on:** {w['ends_on']}")
            for h in w["hottest_moments"]:
                st.markdown(f"- {h['at']} · {h['score']}/10 · {h['text']}" + (f" *({h['why']})*" if h.get("why") else ""))
        with c2:
            ep = eps.get(w["episode"])
            if ep:
                curve = heat_curve(ep, grades.get(str(w["episode"]), {}))
                df = pd.DataFrame({"second": [i * STEP for i in range(len(curve))], "heat": curve,
                                   "in window": [v if w["start"] <= i * STEP < w["end"] else None
                                                 for i, v in enumerate(curve)]}).set_index("second")
                st.line_chart(df, height=200)
                with st.expander("What plays in this window"):
                    st.code("\n".join(block_line(b) for b in sorted_blocks(ep)
                                      if w["start"] - 0.05 <= b.start < w["end"] - 0.05), language=None)

st.subheader("Top 3 per episode")
rows = [{"episode": int(n), "rank": i, "start": x["start_ts"], "end": x["end_ts"], "duration_s": x["duration"],
         "score": x["score"], "heated moments": ", ".join(x["tags"]), "opens on": x["opens_on"][:80]}
        for n, v in res["per_episode"].items() for i, x in enumerate(v, 1)]
st.dataframe(pd.DataFrame(rows), hide_index=True)

c1, c2, c3 = st.columns(3)
c1.download_button("Top windows (CSV)", (run_dir / "sensational_top.csv").read_bytes(), f"{pick}-top.csv")
c2.download_button("Per episode (CSV)", (run_dir / "sensational_per_episode.csv").read_bytes(), f"{pick}-per-episode.csv")
c3.download_button("Report (Markdown)", (run_dir / "sensational.md").read_bytes(), f"{pick}.md")
