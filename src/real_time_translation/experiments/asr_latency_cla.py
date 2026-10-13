"""Segmentation-robust ASR word latency via Continuous Levenshtein Alignment (CLA).

`results.csv` today only tracks coarse per-experiment averages
(avg_end_to_end_latency_seconds / avg_mt_latency_seconds are not even in
that CSV -- they only live inside each experiment JSON's `results` dict).
Those averages are themselves influenced by how the pipeline segments audio
into ASR batches, which is exactly the "segmentation bias" that
research_agent/state/papers.json's `polak2026-meta-evaluation-latency-metrics`
finds distorts cross-system latency comparisons in the wider literature.

`machacek-polak2025-cuni-offline-cla-latency` (Section 5.1) describes a
retroactive, character-level "Continuous Levenshtein Alignment" (CLA) method
for computing ASR word latency: align a timestamped gold transcript against
the ASR system's output at the character level, preferring continuous runs
of copy/substitute edits over ones interrupted by inserts/deletes (which
would otherwise align a word to a coincidentally-nearby but unrelated
insertion/deletion and produce a nonsensical latency value). This module
implements that idea against this repo's own experiment corpus, using
`experiments/refs/wjZofJX0v4M.en.vtt` (YouTube's own captions) as the gold
transcript -- no new ASR/LLM calls are needed, this is a $0 retroactive
analysis (h-cla-asr-latency-metric).

Two approximations relative to the primary source, stated explicitly because
this repo's own hard rule is to never present an approximation as if it were
the real thing:

1. **Gold timestamps are per-VTT-cue, not per-word.** YouTube's caption VTT
   gives a timestamp per caption block (typically 1-4 seconds, several
   words), not per-word forced-alignment timestamps like the primary source
   presumably uses. We linearly interpolate each cue's words evenly across
   its `[start, end)` span. This is a real source of noise for very short
   cues with many words; do not treat single-experiment CLA numbers as
   precise to better than roughly a cue's duration.
2. **Latency is reported per ASR-settled group (batch), not per word.** We
   align gold text against the concatenation of each Deepgram
   utterance-group's *final* (most-revised) interim hypothesis, and report
   the latency of the *start* of each group's aligned gold span, not every
   individual word. This is coarser than true per-word CLA, but it is
   enough to compare relative latency across different pipeline
   configurations (e.g. the endpointing-ms sweep) on a shared, config-
   independent footing, which is this hypothesis's actual goal.

Despite both approximations, this is still a genuinely different signal from
`avg_end_to_end_latency_seconds`: that field is computed by the pipeline
itself from its own internal batch timers, so two experiments with
different chunking would report incomparable "average" batches; CLA instead
measures against one fixed external gold transcript regardless of how the
pipeline chose to segment.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from dataclasses import dataclass
from pathlib import Path

from real_time_translation.experiments.stats import mean, median

_TS_RE = re.compile(r"(\d\d:\d\d:\d\d\.\d\d\d) --> (\d\d:\d\d:\d\d\.\d\d\d)")
_TAG_RE = re.compile(r"<[^>]+>")


def _vtt_ts_to_seconds(ts: str) -> float:
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


@dataclass(frozen=True)
class GoldWord:
    text: str
    timestamp: float  # seconds, absolute (full-video) time


def parse_vtt_gold_words(path: Path) -> list[GoldWord]:
    """Parse a WebVTT caption file into a flat, time-ordered word list, with
    each word's timestamp linearly interpolated across its cue's
    [start, end) span (see module docstring, approximation 1)."""
    raw = path.read_text(encoding="utf-8")
    words: list[GoldWord] = []
    last_text: str | None = None
    for block in raw.split("\n\n"):
        m = _TS_RE.search(block)
        if not m:
            continue
        start = _vtt_ts_to_seconds(m.group(1))
        end = _vtt_ts_to_seconds(m.group(2))
        lines = block.split("\n")
        text_lines = [
            _TAG_RE.sub("", ln).strip()
            for ln in lines[lines.index(m.group(0)) + 1 :]
            if ln.strip()
        ]
        text = " ".join(text_lines).strip()
        if not text or text == last_text:
            continue  # YouTube auto-captions repeat the same cue text across
            # consecutive rolling-caption blocks; skip exact repeats so words
            # aren't double-counted/double-timestamped.
        last_text = text
        cue_words = text.split()
        span = max(end - start, 1e-6)
        for i, w in enumerate(cue_words):
            frac = (i + 0.5) / len(cue_words)
            words.append(GoldWord(text=w, timestamp=start + frac * span))
    return words


@dataclass(frozen=True)
class GoldWindow:
    """Gold transcript windowed and time-shifted into an experiment clip's
    own local time (0 == start of the ffmpeg-extracted clip), matching how
    `TimedEvent.playback_offset`/`asr_start_time`/`asr_end_time` are defined
    in video_segment.py."""

    text: str  # space-joined words, local time order
    char_timestamps: list[float]  # timestamp of the gold word owning each char


def window_gold(
    words: list[GoldWord], clip_start: float, clip_end: float | None
) -> GoldWindow:
    in_window = [
        w
        for w in words
        if w.timestamp >= clip_start and (clip_end is None or w.timestamp < clip_end)
    ]
    parts: list[str] = []
    char_timestamps: list[float] = []
    for i, w in enumerate(in_window):
        if i > 0:
            parts.append(" ")
            char_timestamps.append(w.timestamp - clip_start)
        parts.append(w.text)
        char_timestamps.extend([w.timestamp - clip_start] * len(w.text))
    return GoldWindow(text="".join(parts), char_timestamps=char_timestamps)


@dataclass(frozen=True)
class AsrGroup:
    text: str
    emission_time: float  # asr_end_time of the group's final (most-revised) event


def asr_settled_groups(events: list[dict]) -> list[AsrGroup]:
    """Group asr_interim events by shared asr_start_time (same convention as
    flicker_metrics.group_asr_interim) and keep each group's last (most
    fully-revised) hypothesis text plus that last event's `asr_end_time` as
    its emission time.

    Deliberately NOT `playback_offset` (real wall-clock time since the
    experiment process started): checking this against these specific
    experiments' own event logs (2026-09-12) showed `wall_clock_lag`
    (`playback_offset - asr_start_time`) drifting increasingly *negative*
    over the course of a run (e.g. 20260903_asr_keyterms_off2.json:
    elapsed_seconds=234.1 wall-clock for 267.8s of asr_end_time content --
    i.e. these runs were not paced at a strict real-time `--speed 1.0`, or
    Deepgram's own timestamps ran ahead of wall-clock for other reasons).
    `playback_offset` is therefore not on the same clock as the gold VTT
    transcript's absolute video-time, and mixing the two produced nonsense
    (latencies going more negative every group, unrelated to the actual
    config being tested). `asr_end_time` is a content-position clock, same
    basis as the (clip-shifted) gold-word timestamps, so it measures "how
    much audio-content elapsed between a word being spoken and the
    recognizer having buffered/committed enough audio to finalize a
    hypothesis containing it" -- a chunking/endpointing-driven delay, which
    is exactly the axis the endpointing-ms and chunk-length sweep
    experiments this hypothesis targets are varying, and it is unaffected
    by unrelated wall-clock execution-speed noise.
    """
    groups: list[AsrGroup] = []
    current_start: float | None = None
    current_text = ""
    current_time = 0.0
    for e in events:
        if e.get("kind") != "asr_interim":
            continue
        start = e.get("asr_start_time", 0.0)
        if current_start is not None and start != current_start and current_text:
            groups.append(AsrGroup(text=current_text, emission_time=current_time))
        if current_start is None or start != current_start:
            current_start = start
        current_text = e.get("text", "")
        current_time = e.get("asr_end_time", 0.0) or 0.0
    if current_text:
        groups.append(AsrGroup(text=current_text, emission_time=current_time))
    return groups


def _cla_align(gold: str, asr: str) -> list[int | None]:
    """Character-level Continuous Levenshtein Alignment.

    Returns, for each character index in `asr`, the aligned character index
    in `gold` (or None for an inserted/unaligned char). Standard
    edit-distance DP (match cost 0, substitute/insert/delete cost 1), but
    the traceback prefers extending the *same* operation as the previous
    step (continuing a diagonal copy/substitute run, or continuing an
    insert/delete run) over switching operations when costs tie -- this is
    the "continuous" preference from the primary source's Section 5.1,
    meant to avoid aligning a gold word to a coincidentally-nearby but
    unrelated inserted/deleted span.
    """
    n, m = len(gold), len(asr)
    # dp[i][j] = edit distance between gold[:i] and asr[:j].
    # op[i][j] in {'D'(diag/match-or-sub), 'U'(up: gold-only, i.e. asr-side
    # delete/skip), 'L'(left: asr-only, i.e. asr-side insert)}.
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    op = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i
        op[i][0] = "U"
    for j in range(1, m + 1):
        dp[0][j] = j
        op[0][j] = "L"
    for i in range(1, n + 1):
        gi = gold[i - 1]
        row = dp[i]
        prow = dp[i - 1]
        orow = op[i]
        for j in range(1, m + 1):
            sub_cost = 0 if gi == asr[j - 1] else 1
            diag = prow[j - 1] + sub_cost
            up = prow[j] + 1
            left = row[j - 1] + 1
            best = min(diag, up, left)
            # Continuity preference: if multiple ops tie for best, prefer
            # whichever matches the op that produced this cell's
            # up/left/diag neighbor's own predecessor -- approximated here
            # by a fixed tie-break order that favors diag (match/sub) first
            # since that is what "continuous copy/substitute runs" means,
            # then whichever of up/left continues the neighbor's own op.
            if diag == best:
                chosen = "D"
            elif up == best and op[i - 1][j] in ("U", ""):
                chosen = "U"
            elif left == best and orow[j - 1] in ("L", ""):
                chosen = "L"
            elif up == best:
                chosen = "U"
            else:
                chosen = "L"
            row[j] = best
            orow[j] = chosen
    # Traceback.
    alignment: list[int | None] = [None] * m
    i, j = n, m
    while i > 0 or j > 0:
        o = op[i][j]
        if i > 0 and j > 0 and o == "D":
            alignment[j - 1] = i - 1
            i -= 1
            j -= 1
        elif i > 0 and (j == 0 or o == "U"):
            i -= 1
        else:
            j -= 1
    return alignment


@dataclass(frozen=True)
class ClaLatencyReport:
    json_path: str
    experiment_name: str
    num_groups: int
    num_latencies: int
    latency_mean: float | None
    latency_median: float | None



# Cells the DP in _cla_align may allocate before we refuse to run it. Sized
# well above every legitimate wjZofJX0v4M-clip experiment window (worst
# observed: ~26M cells for the 600s-request asr_keyterms_off* runs) but far
# below what a wrongly-paired video (e.g. a >20min different-domain talk
# aligned against this video's gold transcript) would demand -- found the
# hard way: 20260724_llm2024_8_part1_full.json is a *different* video and
# produced a 1.5-BILLION-cell request when the video-id guard below was
# missing, which got the whole process OOM-killed.
_MAX_CLA_CELLS = 60_000_000


def analyze_experiment(
    path: Path, gold_words: list[GoldWord], gold_video_id: str
) -> ClaLatencyReport | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    inp = data.get("input", {})
    if gold_video_id not in str(inp):
        return None  # this experiment used a different source clip entirely
    clip_start = float(inp.get("start_seconds", 0.0) or 0.0)
    events = data.get("results", {}).get("events", [])
    groups = asr_settled_groups(events)
    if not groups:
        return None

    # Window the gold transcript to how much audio was *actually* recognized
    # (max asr_end_time across this run's own asr_interim events, +3s
    # buffer), not the experiment's *requested* duration_seconds/end_seconds
    # -- several of this repo's own experiment JSONs (e.g.
    # 20260903_asr_keyterms_on.json, requested 600s) stopped well short of
    # their requested window (real content: ~144s) for reasons unrelated to
    # this metric (see that hypothesis's own notes elsewhere), and windowing
    # by the requested duration would pad the gold side with ~450s of
    # dead-air gold text that was never given a chance to be transcribed,
    # ballooning the alignment matrix for no benefit.
    asr_interim = [e for e in events if e.get("kind") == "asr_interim"]
    max_asr_end = max((e.get("asr_end_time") or 0.0) for e in asr_interim)
    clip_end = clip_start + max_asr_end + 3.0

    gold = window_gold(gold_words, clip_start, clip_end)
    if not gold.text:
        return None

    asr_text = " ".join(g.text for g in groups)
    if len(gold.text) * len(asr_text) > _MAX_CLA_CELLS:
        print(
            f"skip {path}: gold/asr window too large for CLA "
            f"({len(gold.text)}x{len(asr_text)} chars) -- likely a mismatched clip"
        )
        return None
    alignment = _cla_align(gold.text, asr_text)

    latencies: list[float] = []
    offset = 0
    for g in groups:
        span_start = offset
        offset += len(g.text)
        offset += 1  # the joining space
        span_end = span_start + len(g.text)
        gold_idx = next(
            (
                alignment[k]
                for k in range(span_start, span_end)
                if k < len(alignment) and alignment[k] is not None
            ),
            None,
        )
        if gold_idx is None or gold_idx >= len(gold.char_timestamps):
            continue
        spoken_time = gold.char_timestamps[gold_idx]
        latency = g.emission_time - spoken_time
        if latency >= 0:  # negative = alignment noise (gold word after ASR emitted it)
            latencies.append(latency)

    return ClaLatencyReport(
        json_path=str(path),
        experiment_name=data.get("experiment_name", path.stem),
        num_groups=len(groups),
        num_latencies=len(latencies),
        latency_mean=mean(latencies),
        latency_median=median(latencies),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=None,
        help="Experiment JSON file(s) or glob(s). Defaults to experiments/*.json",
    )
    parser.add_argument(
        "--gold-vtt",
        type=Path,
        default=Path("experiments/refs/wjZofJX0v4M.en.vtt"),
        help="Gold WebVTT transcript to align against.",
    )
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("experiments/asr_latency_cla.csv"),
        help="Sidecar CSV to write (kept separate from results.csv, same "
        "precedent as flicker_metrics.py, since results.csv's schema is "
        "written by the live experiment runners and shared across all "
        "experiments, not just ones with a matching gold transcript).",
    )
    args = parser.parse_args()

    gold_words = parse_vtt_gold_words(args.gold_vtt)
    # e.g. "wjZofJX0v4M" from ".../wjZofJX0v4M.en.vtt" -- used to skip any
    # experiment JSON whose input clip isn't this same video (see
    # _MAX_CLA_CELLS's docstring for why this guard is load-bearing, not
    # just tidiness).
    gold_video_id = args.gold_vtt.name.split(".")[0]

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    reports: list[ClaLatencyReport] = []
    skipped_no_gold_overlap = 0
    for path in paths:
        try:
            report = analyze_experiment(path, gold_words, gold_video_id)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")
            continue
        if report is None:
            skipped_no_gold_overlap += 1
            continue
        reports.append(report)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "num_groups",
                "num_latencies",
                "asr_word_latency_cla_mean_s",
                "asr_word_latency_cla_median_s",
            ]
        )
        for r in reports:
            writer.writerow(
                [
                    r.experiment_name,
                    r.json_path,
                    r.num_groups,
                    r.num_latencies,
                    r.latency_mean,
                    r.latency_median,
                ]
            )

    print(
        f"analyzed {len(reports)} experiment(s) against {args.gold_vtt} -> "
        f"{args.out_csv} ({skipped_no_gold_overlap} skipped: no gold-transcript "
        "overlap / no ASR events)"
    )
    for r in reports:
        if r.latency_mean is not None:
            print(
                f"  {r.experiment_name}: mean={r.latency_mean:.3f}s "
                f"median={r.latency_median:.3f}s "
                f"(n={r.num_latencies}/{r.num_groups} groups)"
            )


if __name__ == "__main__":
    main()
