"""Promo Finder - Streamlit front end.  Run:  streamlit run app.py"""
from __future__ import annotations

import io
import json
import os
import random
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from promo_finder.config import load_config
from promo_finder.evaluate import evaluate
from promo_finder.parse import load_parsed
from promo_finder.render import block_line, clip_text, sorted_blocks
from promo_finder.schemas import DIMENSIONS, RankedPromo, fmt_ts

ROOT = Path(__file__).resolve().parent

# Streamlit Cloud: keys live in the app's secrets store; expose them as env vars
try:
    for _k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY", "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL"):
        if _k in st.secrets and not os.getenv(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass
RUNS = ROOT / "runs"
RUNS.mkdir(exist_ok=True)
REASONS = ["weak hook", "resolves too early", "needs context", "bad cut", "duplicate", "unsafe for ads", "other"]

st.set_page_config(page_title="Promo Finder", layout="wide")
st.title("Promo Finder")
st.caption("Finds 20-60 s promos in microdrama screenplays: hook in the first 3 s, cliffhanger at the end, "
           "makes sense to a stranger. The model picks moments; code sets every timestamp.")


# ------------------------------------------------------------------ sidebar: run
def save_uploads(files) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="upload_"))
    for f in files:
        data = f.getvalue()
        if f.name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for m in z.namelist():
                    rel = Path(m)
                    if rel.suffix.lower() == ".txt" and not rel.name.startswith(".") and "__MACOSX" not in rel.parts \
                            and not rel.is_absolute() and ".." not in rel.parts:
                        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                        (tmp / rel).write_bytes(z.read(m))
        else:
            (tmp / Path(f.name).name).write_bytes(data)
    return tmp


with st.sidebar:
    st.header("Run")
    mode = st.radio("Mode", ["AI pipeline", "No-AI baseline"], horizontal=True)
    cfg = load_config()
    if mode == "AI pipeline":
        provider = st.selectbox("Provider", ["gemini", "anthropic", "openai_compat", "mock"],
                                index=["gemini", "anthropic", "openai_compat", "mock"].index(cfg["llm"]["provider"]))
        default_model = {"gemini": "gemini-3.8-flash", "anthropic": "claude-sonnet-5-5",
                         "openai_compat": "", "mock": "mock"}[provider]
        model = st.text_input("Model", value=cfg["llm"]["model"] if provider == cfg["llm"]["provider"] else default_model)
        judge_model = st.text_input("Judge model (optional, a different family is better)", value="")
        base_url = st.text_input("Base URL (openai_compat only)", value="") if provider == "openai_compat" else ""
        key = st.text_input("API key (kept in this session only)", type="password",
                            value="" if provider == "mock" else "")
        samples = st.slider("Judge samples per promo", 1, 3, int(cfg["pipeline"]["judge_samples"]))
        per_ep = st.slider("Proposals per episode", 2, 8, int(cfg["pipeline"]["promos_per_episode"]))
        montage = st.slider("Cross-episode montages", 0, 12, int(cfg["pipeline"]["montage_promos"]))
    episodes_filter = st.text_input("Episodes (blank = all; e.g. 1-5 or 1,10,20)", value="")
    title = st.text_input("Series title (for reports)", value="")
    uploads = st.file_uploader("Screenplays: a .zip or several .txt files", type=["zip", "txt"],
                               accept_multiple_files=True)
    local = ROOT / "data" / "raw"
    use_local = local.exists() and not uploads and st.checkbox(f"Use local folder data/raw", value=True)
    go = st.button("Run", type="primary", width="stretch")

if go:
    src = save_uploads(uploads) if uploads else (local if use_local else None)
    if not src:
        st.sidebar.error("Upload screenplays first.")
    else:
        from promo_finder.pipeline import run
        if mode == "AI pipeline":
            # reload for the chosen provider, so its own default endpoint and key variable apply
            cfg = load_config(overrides={"llm": {"provider": provider, "model": model,
                                                 "judge_model": judge_model or None, "base_url": base_url or None}})
            if provider != cfg["llm"]["provider"]:   # an LLM_PROVIDER env var must not win over the sidebar
                cfg["llm"]["provider"] = provider
            if key:
                cfg["llm"]["api_key"] = key
            cfg["pipeline"].update({"judge_samples": samples, "promos_per_episode": per_ep, "montage_promos": montage})
        if episodes_filter:
            cfg["episode_filter"] = episodes_filter
        if title:
            cfg["title"] = title
        name = datetime.now().strftime("%Y%m%d-%H%M%S") + ("-ai" if mode == "AI pipeline" else "-baseline")
        bar, log = st.progress(0.0, text="starting"), st.empty()
        lines = []

        def progress(msg, frac=None):
            lines.append(msg)
            log.code("\n".join(lines[-8:]))
            if frac is not None:
                bar.progress(min(max(frac, 0.0), 1.0), text=msg)
        try:
            if mode == "AI pipeline" and provider != "mock" and not cfg["llm"].get("api_key"):
                raise RuntimeError("Enter an API key, or pick the mock provider / No-AI baseline.")
            run(src, RUNS / name, cfg, mode="llm" if mode == "AI pipeline" else "baseline", progress=progress)
            st.session_state["run"] = name
            st.success(f"Finished run {name}")
        except Exception as e:  # show the reason, keep the app alive
            st.error(f"Run failed: {e}")

# ------------------------------------------------------------------ pick a run
runs = sorted([p.name for p in RUNS.iterdir() if (p / "promos_ranked.json").exists()], reverse=True)
if not runs:
    st.info("No runs yet. Upload screenplays in the sidebar and press Run.")
    st.stop()
pick = st.selectbox("Run", runs, index=runs.index(st.session_state["run"]) if st.session_state.get("run") in runs else 0)
run_dir = RUNS / pick


@st.cache_data(show_spinner=False)
def load_run(path: str, mtime: float):
    d = Path(path)
    rows = [RankedPromo.model_validate(r) for r in json.loads((d / "promos_ranked.json").read_text(encoding="utf-8"))]
    bible = json.loads((d / "bible.json").read_text(encoding="utf-8"))
    manifest = json.loads((d / "run_manifest.json").read_text(encoding="utf-8"))
    return rows, bible, manifest


rows, bible, manifest = load_run(str(run_dir), (run_dir / "promos_ranked.json").stat().st_mtime)
episodes = load_parsed(run_dir / "parsed")
shipped = [r for r in rows if r.status == "shipped"]
if not rows:
    st.warning("This run produced no candidates. Check its manifest below for the reason.")
    st.json(manifest.get("failures", {}))
    st.stop()
f = manifest["funnel"]
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Episodes", manifest["episodes"])
c2.metric("Proposed", f["proposed"])
c3.metric("Valid", f["valid"])
c4.metric("Passed gates", f["passed_gates"])
c5.metric("Ranked", f["shipped"])
who = manifest["config"]["llm"]["model"] if manifest["mode"] == "llm" else "no-AI baseline"
st.caption(f"{manifest['series']} · {who} · {manifest['seconds']} s · finished {manifest['finished_utc']}")

tabs = st.tabs(["Ranked promos", "Promo detail", "Review", "Label episodes", "Evaluation", "Data", "Downloads"])


def seg_str(r: RankedPromo) -> str:
    return " | ".join(f"Ep {s.episode} {fmt_ts(s.start)}-{fmt_ts(s.end)}" for s in r.candidate.segments)


# ------------------------------------------------------------------ ranked
with tabs[0]:
    st.subheader("Post these first")
    for r in [x for x in shipped if x.slate]:
        with st.container(border=True):
            st.markdown(f"**{r.rank}. {r.candidate.title}** — score {r.composite:.2f} · {r.candidate.total:.1f} s")
            st.markdown(f"`{seg_str(r)}`")
            st.markdown(f"**Hook:** {r.candidate.hook}  \n**Cliffhanger:** {r.candidate.cliffhanger}  \n"
                        f"**Why:** {r.candidate.why}")
            if r.review_flags:
                st.warning("Check before posting: " + "; ".join(r.review_flags))
    st.subheader("All candidates")
    show = st.multiselect("Status", ["shipped", "not_selected", "below_gate", "duplicate", "invalid"], ["shipped"])
    df = pd.DataFrame([{
        "rank": r.rank, "status": r.status, "id": r.promo_id, "source": r.candidate.source,
        "title": r.candidate.title, "cut": seg_str(r), "length_s": r.candidate.total,
        "score": r.composite, **{k: (r.judgement.scores[k] if r.judgement else None) for k in DIMENSIONS},
        "flags": len(r.review_flags), "why not": "; ".join([c.detail for c in r.validation.failures] +
                                                           ([f"same story as {r.duplicate_of}"] if r.duplicate_of else []) +
                                                           ([x for x in r.review_flags if "below" in x]))}
        for r in rows if r.status in show])
    st.dataframe(df, hide_index=True)

# ------------------------------------------------------------------ detail
with tabs[1]:
    ids = [r.promo_id for r in rows]
    default = shipped[0].promo_id if shipped else ids[0]
    pid = st.selectbox("Promo", ids, index=ids.index(default),
                       format_func=lambda i: next(f"{i} · {r.status} · {r.candidate.title}" for r in rows if r.promo_id == i))
    r = next(x for x in rows if x.promo_id == pid)
    c = r.candidate
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"### {c.title}")
        st.table(pd.DataFrame([{"#": i, "episode": s.episode, "start": fmt_ts(s.start), "end": fmt_ts(s.end),
                                "seconds": s.duration} for i, s in enumerate(c.segments, 1)]))
        st.markdown(f"**Total:** {c.total:.2f} s · **status:** {r.status}" + (f" · **rank** {r.rank}" if r.rank else ""))
        st.markdown(f"**Hook:** {c.hook}  \n**Cliffhanger:** {c.cliffhanger}  \n**Stands alone?** "
                    f"{c.standalone_context}  \n**Why:** {c.why}")
        if c.repairs:
            st.info("Code adjusted the model's proposal: " + "; ".join(c.repairs))
        st.markdown("**Clip transcript** (what plays, with times from the clip start)")
        st.code(clip_text(c, episodes, bible.get("aliases"), blind=False), language=None)
    with right:
        if r.judgement:
            j = r.judgement
            st.markdown(f"**Judge:** {j.judge} · composite **{r.composite:.2f}**" if r.composite else "**Judge**")
            st.bar_chart(pd.DataFrame({"score": [j.scores[k] for k in DIMENSIONS]}, index=list(DIMENSIONS)),
                         horizontal=True)
            for k in DIMENSIONS:
                why = j.samples[0].reasons.get(k, "")
                gap = f" (samples differ by {j.spread[k]:.0f})" if j.spread.get(k) else ""
                if why:
                    st.markdown(f"- **{k}**: {why}{gap}")
            if j.samples and j.samples[0].pitch:
                st.markdown(f"*Pitch:* {j.samples[0].pitch}")
        st.markdown("**Validation**")
        if r.validation.passed:
            st.success("All rules V1-V11 pass")
        for x in r.validation.failures:
            st.error(f"{x.rule}: {x.detail}")
        for x in r.validation.warnings:
            st.warning(f"{x.rule}: {x.detail}")
        for x in r.review_flags:
            st.caption(f"flag: {x}")

# ------------------------------------------------------------------ review
with tabs[2]:
    st.markdown("Judge each clip as a viewer would. The list mixes the top 10 with 5 valid candidates that did not "
                "make the list, shuffled and unscored, so precision is measured against a blind control.")
    rev_path = run_dir / "reviews.json"
    reviews = json.loads(rev_path.read_text(encoding="utf-8")) if rev_path.exists() else {}
    top = [r.promo_id for r in shipped[:10]]
    others = [r.promo_id for r in rows if r.status in ("not_selected", "below_gate") and r.validation.passed]
    rnd = random.Random(pick)
    controls = rnd.sample(others, min(5, len(others)))
    queue = top + controls
    rnd.shuffle(queue)
    for i, qid in enumerate(queue, 1):
        r = next(x for x in rows if x.promo_id == qid)
        with st.expander(f"Clip {i} · {r.candidate.total:.0f} s · {seg_str(r)}", expanded=False):
            st.code(clip_text(r.candidate, episodes, bible.get("aliases"), blind=True), language=None)
            prev = reviews.get(qid, {})
            dec = st.radio("Would you post it?", ["-", "approve", "reject"], horizontal=True, key=f"d{qid}",
                           index=["-", "approve", "reject"].index(prev.get("decision", "-")))
            reason = st.selectbox("Main problem (if rejected)", [""] + REASONS, key=f"r{qid}",
                                  index=([""] + REASONS).index(prev.get("reason", "")) if prev.get("reason", "") in REASONS else 0)
            if dec != "-":
                reviews[qid] = {"decision": dec, "reason": reason, "control": qid in controls}
    if st.button("Save reviews"):
        rev_path.write_text(json.dumps(reviews, indent=1), encoding="utf-8")
        evaluate(run_dir, reference=ROOT / "reference.json", reviews=rev_path)
        st.success(f"Saved {len(reviews)} reviews and refreshed the evaluation.")

# ------------------------------------------------------------------ label
with tabs[3]:
    st.markdown("Label the best hook and best cliffhanger in a few episodes, before looking at the tool's picks. "
                "These become reference.json, used for hit@3 in the evaluation.")
    ref_path = ROOT / "reference.json"
    ref = json.loads(ref_path.read_text(encoding="utf-8")) if ref_path.exists() else {"episodes": {}}
    n = st.selectbox("Episode", sorted(episodes))
    ep = episodes[n]
    opts = [b.block_id for b in sorted_blocks(ep) if b.kind != "camera"]
    fmt = {b.block_id: block_line(b, bible.get("aliases", {}).get(str(n))) for b in sorted_blocks(ep)}
    cur = ref["episodes"].get(str(n), {})
    hook = st.selectbox("Best hook block", [""] + opts, format_func=lambda x: fmt.get(x, "-"),
                        index=([""] + opts).index(cur.get("hook", "")) if cur.get("hook", "") in opts else 0)
    cliff = st.selectbox("Best cliffhanger block", [""] + opts, format_func=lambda x: fmt.get(x, "-"),
                         index=([""] + opts).index(cur.get("cliffhanger", "")) if cur.get("cliffhanger", "") in opts else 0)
    must = st.text_input("Must-use moments, as start-end in seconds (e.g. 12.5-48, 70-95)",
                         value=", ".join(f"{a}-{b}" for a, b in cur.get("must_promo", [])))
    if st.button("Save label"):
        spans = []
        for part in [p.strip() for p in must.split(",") if p.strip()]:
            a, b = part.split("-")
            spans.append([float(a), float(b)])
        ref["episodes"][str(n)] = {"hook": hook or None, "cliffhanger": cliff or None, "must_promo": spans}
        ref_path.write_text(json.dumps(ref, indent=1), encoding="utf-8")
        st.success(f"Saved. {len(ref['episodes'])} episodes labelled.")
    with st.expander("Episode screenplay"):
        st.code("\n".join(fmt[b.block_id] for b in sorted_blocks(ep)), language=None)

# ------------------------------------------------------------------ evaluation
with tabs[4]:
    if st.button("Recompute evaluation"):
        base = next((RUNS / x for x in runs if x.endswith("baseline") and x != pick), None)
        evaluate(run_dir, reference=ROOT / "reference.json", reviews=run_dir / "reviews.json", baseline_dir=base)
    if (run_dir / "eval_report.md").exists():
        st.markdown((run_dir / "eval_report.md").read_text(encoding="utf-8"))

# ------------------------------------------------------------------ data
with tabs[5]:
    st.markdown("Per-episode audit")
    st.dataframe(pd.read_csv(run_dir / "audit.csv"), hide_index=True)
    if manifest.get("parse_issues"):
        st.warning("\n".join(manifest["parse_issues"]))
    with st.expander("Series bible (who is who)"):
        st.json(bible)
    if manifest.get("usage"):
        st.markdown("Model usage")
        st.json(manifest["usage"])

# ------------------------------------------------------------------ downloads
with tabs[6]:
    st.download_button("Ranked promos (CSV)", (run_dir / "promos_ranked.csv").read_bytes(), f"{pick}-promos.csv")
    st.download_button("Report (Markdown)", (run_dir / "report.md").read_bytes(), f"{pick}-report.md")
    buf = io.BytesIO()
    shutil.make_archive(str(Path(tempfile.gettempdir()) / pick), "zip", run_dir)
    st.download_button("Whole run folder (zip)", (Path(tempfile.gettempdir()) / f"{pick}.zip").read_bytes(),
                       f"{pick}.zip")
    st.caption("Run folders contain screenplay text. Share them only privately.")
