"""Compare gpt-live-1 (interpreter) against the Deepgram->LLM pipeline.

Same audio windows, same reference. Per clip <X> the inputs are:
  experiments/clips/llm2025_cmp/<X>_en_ground_truth.txt   timestamped English
  experiments/clips/llm2025_cmp/<X>_ja_reference.txt      reference Japanese
  a gpt-live experiment JSON (gpt_live_video.py) per run
  the pipeline's full-lecture experiment JSON (window cut by --start)

Metrics
  quality      chrF++ (sacrebleu, word_order=2) against the reference
  captions     reading speed, display time, gaps, short cards, sentence-complete
               cards (same readable formatter and anomaly gate for both)
  latency      sentence ready-time minus the audio time of the matching
               reference sentence. Matching = monotone DP over sentence chrF;
               reference sentence time = English char position mapped onto the
               ground-truth timestamps. The pipeline's own true latency
               (playback_offset - asr_end_time) is reported alongside to show
               how far this anchor is off; the anchor error is shared by both
               systems, so the *difference* is the trustworthy number.
All intervals are 95% bootstrap CIs (resampling sentences/cards).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics as st
from pathlib import Path

from sacrebleu.metrics import CHRF

from real_time_translation.captions.subtitle_formatter import (
    extract_utterance_cues,
    format_readable,
    weighted_len,
)
from real_time_translation.experiments.caption_issue_metrics import score as gate

CLIP_DIR = Path("experiments/clips/llm2025_cmp")
WINDOW = 180.0
SENT = re.compile(r"(?<=[。！？])")
rng = random.Random(0)


def boot(xs, fn=st.mean, n=2000):
    xs = list(xs)
    if len(xs) < 2:
        return None
    vals = sorted(fn([rng.choice(xs) for _ in xs]) for _ in range(n))
    return [round(vals[int(0.025 * n)], 3), round(vals[int(0.975 * n)], 3)]


def boot_diff(a, b, fn=st.mean, n=2000):
    a, b = list(a), list(b)
    vals = sorted(
        fn([rng.choice(a) for _ in a]) - fn([rng.choice(b) for _ in b])
        for _ in range(n)
    )
    return [round(vals[int(0.025 * n)], 3), round(vals[int(0.975 * n)], 3)]


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def ref_sentence_times(clip: str) -> list[tuple[float, str]]:
    """(audio time at which the sentence's English ends, ja sentence)."""
    lines = []
    for ln in (CLIP_DIR / f"{clip}_en_ground_truth.txt").read_text().splitlines():
        m = re.match(r"\[(\d+):(\d+)\]\s*(.*)", ln)
        if m:
            lines.append((int(m[1]) * 60 + int(m[2]), m[3]))
    starts = [t for t, _ in lines] + [WINDOW]
    cum, tot = [], 0
    for _, txt in lines:
        tot += max(len(txt), 1)
        cum.append(tot)

    def time_at(frac: float) -> float:
        target = frac * tot
        prev = 0
        for i, c in enumerate(cum):
            if target <= c:
                return starts[i] + (starts[i + 1] - starts[i]) * (
                    (target - prev) / max(c - prev, 1)
                )
            prev = c
        return starts[-1]

    ja = (CLIP_DIR / f"{clip}_ja_reference.txt").read_text().strip()
    sents = [s for s in SENT.split(ja) if s.strip()]
    total_ja, run, out = len(ja), 0, []
    for s in sents:
        run += len(s)
        out.append((time_at(run / total_ja), s))
    return out


def align(hyps: list[str], refs: list[str]) -> list[int]:
    """Monotone assignment of each hyp sentence to a ref sentence (max chrF)."""
    ch = CHRF(word_order=2)
    n, m = len(hyps), len(refs)
    sc = [[ch.sentence_score(h, [r]).score for r in refs] for h in hyps]
    NEG = -1e9
    dp = [[NEG] * m for _ in range(n)]
    bp = [[0] * m for _ in range(n)]
    for j in range(m):
        dp[0][j] = sc[0][j]
    for i in range(1, n):
        best, arg = NEG, 0
        for j in range(m):
            if dp[i - 1][j] > best:
                best, arg = dp[i - 1][j], j
            dp[i][j] = best + sc[i][j]
            bp[i][j] = arg
    j = max(range(m), key=lambda k: dp[n - 1][k])
    out = [0] * n
    for i in range(n - 1, -1, -1):
        out[i] = j
        j = bp[i][j]
    return out


def load_gpt(path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    return d["results"]["events"], d


def load_pipeline(path: Path, start: float):
    d = json.loads(path.read_text(encoding="utf-8"))
    ev = []
    for e in d["results"]["events"]:
        if e["kind"] not in ("translation_partial", "translation_complete"):
            continue
        s, en = e["asr_start_time"], e["asr_end_time"]
        if s is None or en is None or en <= start or en > start + WINDOW:
            continue
        ev.append(
            dict(
                e,
                playback_offset=e["playback_offset"] - start,
                asr_start_time=s - start,
                asr_end_time=en - start,
            )
        )
    true_lat = [
        e["playback_offset"] - e["asr_end_time"]
        for e in ev
        if e["kind"] == "translation_complete" and e.get("is_utterance_end")
    ]
    return ev, d, true_lat


def card_stats(events: list[dict]):
    cues = [c for c in format_readable(extract_utterance_cues(events)) if c.start < 200]
    L = [weighted_len(c.text.replace("\n", "")) for c in cues]
    D = [c.end - c.start for c in cues]
    gaps = [b.start - a.end for a, b in zip(cues, cues[1:], strict=False)]
    ups = [length / d for length, d in zip(L, D, strict=False) if d > 0]
    done = [c.text.rstrip()[-1:] in "。！？!?" for c in cues]
    return dict(
        cues=cues, L=L, D=D, gaps=gaps, ups=ups, done=done,
        on_screen=sum(D) / WINDOW,
    )  # fmt: skip


def evaluate(system: str, runs: list[tuple[str, list[dict], dict | None]], clip: str):
    refs = ref_sentence_times(clip)
    ref_text = "".join(s for _, s in refs)
    out = []
    for run_id, ev, _meta in runs:
        raw = extract_utterance_cues(ev)
        hyp_text = "".join(r.text for r in raw)
        a = align([r.text for r in raw], [s for _, s in refs])
        lat = [r.ready_at - refs[j][0] for r, j in zip(raw, a, strict=False)]
        out.append(
            dict(
                system=system, run=run_id, clip=clip,
                chrf=CHRF(word_order=2).corpus_score([hyp_text], [[ref_text]]).score,
                chrf_char_only=CHRF().corpus_score([hyp_text], [[ref_text]]).score,
                len_ratio=len(hyp_text) / len(ref_text),
                n_sent=len(raw), lat=lat, cards=card_stats(ev), events=ev,
            )
        )  # fmt: skip
    return out


def summarize(rows: list[dict], true_lat: list[float] | None = None) -> dict:
    lat = [x for r in rows for x in r["lat"]]
    L = [x for r in rows for x in r["cards"]["L"]]
    D = [x for r in rows for x in r["cards"]["D"]]
    U = [x for r in rows for x in r["cards"]["ups"]]
    G = [x for r in rows for x in r["cards"]["gaps"]]
    done = [x for r in rows for x in r["cards"]["done"]]
    out = {
        "n_runs": len(rows),
        "n_cards": len(L),
        "chrf_mean": round(st.mean(r["chrf"] for r in rows), 2),
        "chrf_by_run": {f"{r['clip']}:{r['run']}": round(r["chrf"], 2) for r in rows},
        "chrf_char_by_run": {
            f"{r['clip']}:{r['run']}": round(r["chrf_char_only"], 2) for r in rows
        },
        "len_ratio_mean": round(st.mean(r["len_ratio"] for r in rows), 3),
        "latency_anchor_mean": round(st.mean(lat), 2),
        "latency_anchor_median": round(st.median(lat), 2),
        "latency_anchor_p90": round(pct(lat, 0.9), 2),
        "latency_anchor_ci": boot(lat),
        "chars_per_card": round(st.mean(L), 2),
        "chars_per_card_ci": boot(L),
        "display_s_mean": round(st.mean(D), 2),
        "display_s_ci": boot(D),
        "units_per_s_mean": round(st.mean(U), 2),
        "units_per_s_ci": boot(U),
        "units_per_s_p95": round(pct(U, 0.95), 2),
        "units_per_s_max": round(max(U), 2),
        "gap_s_mean": round(st.mean(G), 2),
        "gaps_gt_1s": sum(g > 1 for g in G),
        "gaps_gt_2s": sum(g > 2 for g in G),
        "gaps_total": len(G),
        "cards_lt_1s": sum(d < 1 for d in D),
        "cards_lt_1s_rate": round(sum(d < 1 for d in D) / len(D), 3),
        "sentence_complete_card_rate": round(sum(done) / len(done), 3),
        "screen_coverage": round(st.mean(r["cards"]["on_screen"] for r in rows), 3),
    }
    if true_lat:
        out["latency_true_mean"] = round(st.mean(true_lat), 2)
        out["latency_true_median"] = round(st.median(true_lat), 2)
        out["latency_true_p90"] = round(pct(true_lat, 0.9), 2)
        out["latency_true_ci"] = boot(true_lat)
    out["_lat"], out["_L"], out["_D"], out["_U"], out["_G"] = lat, L, D, U, G
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pipeline", type=Path, required=True)
    ap.add_argument(
        "--clips", nargs="+", required=True,
        help="CLIP:START:RUN[,RUN...] e.g. A:600:a1,a2 B:1800:b",
    )  # fmt: skip
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--date", default="20261013")
    args = ap.parse_args()

    gl_rows, pl_rows, pl_true, gates = [], [], [], []
    for spec in args.clips:
        clip, start, runs = spec.split(":")
        start = float(start)
        pev, pd_, tl = load_pipeline(args.pipeline, start)
        pl_true += tl
        pl_rows += evaluate("pipeline", [("p", pev, None)], clip)
        pd_["_win"] = (start, start + WINDOW)
        gates.append(
            dict(clip=clip, system="pipeline",
                 **gate(pd_, start=start, end=start + WINDOW))
        )  # fmt: skip
        glruns = []
        for rid in runs.split(","):
            p = Path(f"experiments/{args.date}_gpt_live1_cmp_{rid}.json")
            gev, gd = load_gpt(p)
            glruns.append((rid, gev, gd))
            gates.append(dict(clip=clip, system="gpt-live-1", run=rid, **gate(gd)))
        gl_rows += evaluate("gpt-live-1", glruns, clip)

    # primary comparison: one gpt run per clip (first), run-to-run shown separately
    primary = [r for r in gl_rows if r["run"] in ("a1", "b", "c")]
    sp = summarize(pl_rows, pl_true)
    sg = summarize(primary)
    sg_all = summarize(gl_rows)
    diffs = {
        "latency_anchor_mean_diff_ci": boot_diff(sg["_lat"], sp["_lat"]),
        "chars_per_card_diff_ci": boot_diff(sg["_L"], sp["_L"]),
        "display_s_diff_ci": boot_diff(sg["_D"], sp["_D"]),
        "units_per_s_diff_ci": boot_diff(sg["_U"], sp["_U"]),
        "gap_s_diff_ci": boot_diff(sg["_G"], sp["_G"]),
    }
    res = {
        "windows_s": WINDOW,
        "pipeline": {k: v for k, v in sp.items() if not k.startswith("_")},
        "gpt_live_1": {k: v for k, v in sg.items() if not k.startswith("_")},
        "gpt_live_1_all_runs": {
            k: v for k, v in sg_all.items() if not k.startswith("_")
        },
        "diffs_gpt_minus_pipeline": diffs,
        "gate": [
            {
                "clip": g["clip"],
                "system": g["system"],
                "run": g.get("run"),
                "anomalies": g["caption_anomalies"],
                **g["captions"],
            }
            for g in gates
        ],  # fmt: skip
        "lat_hist": {
            "pipeline_true": [round(x, 2) for x in pl_true],
            "pipeline_anchor": [round(x, 2) for x in sp["_lat"]],
            "gpt_anchor": [round(x, 2) for x in sg["_lat"]],
        },
        "cards": {
            "pipeline": {"L": sp["_L"], "D": sp["_D"], "U": sp["_U"]},
            "gpt": {"L": sg["_L"], "D": sg["_D"], "U": sg["_U"]},
        },
    }
    args.out.write_text(json.dumps(res, ensure_ascii=False, indent=2), "utf-8")
    print(
        json.dumps(
            {k: res[k] for k in ("pipeline", "gpt_live_1", "diffs_gpt_minus_pipeline")},
            ensure_ascii=False,
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
