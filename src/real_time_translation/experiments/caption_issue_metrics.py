"""Score an experiment JSON against the issues a human viewer actually reported.

A human watched the full captioned LLM2024_8_part1 lecture
(experiments/20260918_llm2025_ep8_zenhan_full_lecture_v2_dict.json) and
wrote down what bothered them (see
experiments/20260924_llm2024_8_part1_human_review.md). Most of those
complaints map onto something countable in the experiment JSON alone, with
no new ASR/LLM calls:

- "重複した表示も結構ある" -> `overlap_duplicate_rate`: a final segment
  whose audio span starts well before the end of an earlier emitted
  segment is re-emitted speech (the same words captioned twice).
- "…。そして、" / "…" fragments -> `dangling_end_rate`: a segment's
  translation ends in a bare connective or an ellipsis placeholder.
- "ちょくちょく早いところがある" -> `short_cue_rate` / `tiny_segment_rate`:
  cards that flash for under a second, and near-empty cards ("そして",
  "私の") that inflate the card count and so the reading backlog.
- "変なところでの改行" -> `midword_breaks` / `glued_latin`: a line break
  inside a Latin word ("Gemm|a"), and Latin words whose separating space
  was stripped ("GoogleResearchAustralia").
- Name/term errors (JoshAnguo, Muscle Lab, AIME, Suspension-Agent, ...) ->
  `term_checks`: occurrences of each known-wrong form in the translation
  output.

Usage:
    python3 -m real_time_translation.experiments.caption_issue_metrics \\
        experiments/20260918_llm2025_ep8_zenhan_full_lecture_v2_dict.json \\
        [--start 450 --end 1310] [--out summary.json]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from real_time_translation.captions.live_presenter import LiveCaptionPresenter
from real_time_translation.captions.subtitle_formatter import (
    _build_readable_items,
    _weighted_len,
    extract_utterance_cues,
    schedule_adaptive,
)

# Known-wrong surface forms from the 2026-09-24 human review, each checked
# case-insensitively against translation output. Kept deliberately literal
# (no fuzzy matching) so a before/after comparison is unambiguous.
DEFAULT_TERM_CHECKS: dict[str, str] = {
    "speaker_name_misheard": r"josh\s*ang|ジョシュ",
    "lab_name_misheard": r"muscle|マッスル",
    "aime_false_positive": r"AIME",
    "suspicion_agent_misheard": r"suspension|サスペンション",
    "yanqi_misheard": r"yanxi",
}

_DANGLING_RE = re.compile(
    r"(そして|また|さらに|それで|つまり|ですので|なので|しかし|でも|例えば|"
    r"まるで|そこで|…|\.\.\.)[、,]?$"
)
_TINY_CHARS = 4
# A card read faster than this (weighted units/s; comfortable is 4) is an
# anomaly to fix, not just a number to report. See CLAUDE.md.
ANOMALY_UNITS_PER_SEC = 8.0
MAX_CARDS_IN_3S = 3
_OVERLAP_EPS = 0.25
_LATIN_PAIR_RE = re.compile(r"[A-Za-z0-9] +[A-Za-z0-9]")


def _in_window(seg: dict, start: float | None, end: float | None) -> bool:
    t = seg.get("asr_start_time", 0.0)
    return (start is None or t >= start) and (end is None or t < end)


def segment_metrics(segments: list[dict]) -> dict:
    n = len(segments)
    overlap = 0
    max_end_so_far = float("-inf")
    for seg in segments:
        if seg["asr_start_time"] < max_end_so_far - _OVERLAP_EPS:
            overlap += 1
        max_end_so_far = max(max_end_so_far, seg["asr_end_time"])
    translations = [(s.get("translation") or "").strip() for s in segments]
    dangling = sum(1 for t in translations if _DANGLING_RE.search(t))
    tiny = sum(1 for t in translations if 0 < len(t) <= _TINY_CHARS)
    empty = sum(1 for t in translations if not t)
    return {
        "n_segments": n,
        "overlap_duplicate_segments": overlap,
        "overlap_duplicate_rate": round(overlap / n, 4) if n else 0.0,
        "dangling_end_segments": dangling,
        "dangling_end_rate": round(dangling / n, 4) if n else 0.0,
        "tiny_segments": tiny,
        "tiny_segment_rate": round(tiny / n, 4) if n else 0.0,
        "empty_translation_segments": empty,
    }


def term_metrics(segments: list[dict], checks: dict[str, str]) -> dict:
    joined = "\n".join((s.get("translation") or "") for s in segments)
    return {
        name: len(re.findall(pattern, joined, flags=re.IGNORECASE))
        for name, pattern in checks.items()
    }


def caption_metrics(events: list[dict]) -> dict:
    """Card-level metrics using the same readable formatter the demos use."""
    utterances = extract_utterance_cues(events)
    items = _build_readable_items(
        utterances,
        max_chars_per_line=13,
        max_lines=2,
        min_duration=5 / 6,
        max_duration=7.0,
        reading_units_per_sec=4.0,
    )
    scheduled = schedule_adaptive(items, min_duration=5 / 6, catchup_window=4.0)
    n = len(scheduled)
    short = sum(1 for cue, _lag in scheduled if cue.end - cue.start < 1.0)
    lags = [lag for _cue, lag in scheduled]
    # A line break between two Latin characters is only a mid-word split
    # when the source had no space there ("Gemm|a"); "Google Research|
    # Australia" breaks at a real space, which is fine.
    sources = [re.sub(r"\s+", " ", u.text) for u in utterances]
    midword = 0
    for cue, _lag in scheduled:
        lines = cue.text.split("\n")
        for a, b in zip(lines, lines[1:], strict=False):
            tail = re.search(r"[A-Za-z0-9]+$", a)
            head = re.match(r"[A-Za-z0-9]+", b)
            if tail and head and not any(
                f"{tail.group()} {head.group()}" in src for src in sources
            ):
                midword += 1
    source_pairs = sum(len(_LATIN_PAIR_RE.findall(u.text)) for u in utterances)
    shown_pairs = sum(
        len(_LATIN_PAIR_RE.findall(cue.text.replace("\n", " ")))
        for cue, _lag in scheduled
    )
    # Reading-speed anomalies (2026-10-13 review: a card for 0.83s, ~30
    # units/s, after one held 7s). Units are the formatter's own reading
    # weights per second, so this matches what the scheduler budgets.
    speeds = [
        _weighted_len(cue.text.replace("\n", "")) / max(cue.end - cue.start, 1e-6)
        for cue, _lag in scheduled
    ]
    starts = [cue.start for cue, _lag in scheduled]
    burst = 0
    for i, t0 in enumerate(starts):
        burst = max(burst, sum(1 for t in starts[i:] if t < t0 + 3.0))
    return {
        "n_cues": n,
        "peak_units_per_sec": round(max(speeds), 2) if speeds else 0.0,
        "over_burst_cards": sum(1 for v in speeds if v > ANOMALY_UNITS_PER_SEC),
        "max_cards_in_3s": burst,
        "max_lag_seconds": round(max(lags), 3) if lags else 0.0,
        "short_cue_rate": round(short / n, 4) if n else 0.0,
        "mean_lag_seconds": round(sum(lags) / n, 3) if n else 0.0,
        "midword_breaks": midword,
        "glued_latin": max(0, source_pairs - shown_pairs),
    }


def live_overlay_metrics(events: list[dict], step: float = 0.05) -> dict:
    """Replay translation events at their real arrival times through both
    the old live overlay (every completed result replaces the screen at
    once) and `LiveCaptionPresenter`, and compare time-on-screen.

    Events carry no utterance_id, so one is synthesized: it advances after
    every `is_utterance_end=True` result, which is how the pipeline itself
    delimits utterances.
    """
    completes = sorted(
        (e for e in events if e.get("kind") == "translation_complete"),
        key=lambda e: e["playback_offset"],
    )
    if not completes:
        return {}

    old_durations = [
        b["playback_offset"] - a["playback_offset"]
        for a, b in zip(completes, completes[1:], strict=False)
    ]

    presenter = LiveCaptionPresenter()
    cards = []
    utterance_id = 0
    i = 0
    t = completes[0]["playback_offset"]
    end = completes[-1]["playback_offset"] + 120.0
    while t <= end:
        while i < len(completes) and completes[i]["playback_offset"] <= t:
            e = completes[i]
            presenter.push(
                e.get("text", ""),
                t,
                utterance_id=utterance_id,
                utterance_end=e.get("is_utterance_end", True),
            )
            if e.get("is_utterance_end", True):
                utterance_id += 1
            i += 1
        card = presenter.poll(t)
        if card is not None:
            cards.append(card)
        if i >= len(completes) and presenter.is_idle(t):
            break
        t = round(t + step, 4)
    new_durations = [
        b.shown_at - a.shown_at for a, b in zip(cards, cards[1:], strict=False)
    ]
    lags = [c.lag for c in cards]

    def rate(xs: list[float], lim: float) -> float:
        return round(sum(1 for x in xs if x < lim) / len(xs), 4) if xs else 0.0

    return {
        "old_overlay_updates": len(completes),
        "old_overlay_on_screen_lt_0_5s_rate": rate(old_durations, 0.5),
        "old_overlay_on_screen_lt_1s_rate": rate(old_durations, 1.0),
        "presenter_cards": len(cards),
        "presenter_on_screen_lt_0_5s_rate": rate(new_durations, 0.5),
        "presenter_on_screen_lt_1s_rate": rate(new_durations, 1.0),
        "presenter_mean_lag_seconds": round(sum(lags) / len(lags), 3),
        "presenter_p95_lag_seconds": round(
            sorted(lags)[int(0.95 * (len(lags) - 1))], 3
        ),
    }


def caption_anomalies(captions: dict) -> list[str]:
    out = []
    if captions["over_burst_cards"]:
        out.append(
            f"{captions['over_burst_cards']} card(s) read faster than "
            f"{ANOMALY_UNITS_PER_SEC} units/s (peak {captions['peak_units_per_sec']})"
        )
    if captions["max_cards_in_3s"] > MAX_CARDS_IN_3S:
        out.append(
            f"{captions['max_cards_in_3s']} cards start within 3s "
            f"(limit {MAX_CARDS_IN_3S})"
        )
    return out


def score(
    data: dict,
    *,
    start: float | None = None,
    end: float | None = None,
    checks: dict[str, str] | None = None,
) -> dict:
    results = data["results"]
    segments = [s for s in results["segments"] if _in_window(s, start, end)]
    events = [
        e
        for e in results["events"]
        if e.get("kind") != "asr_interim" and _in_window(e, start, end)
    ]
    return {
        "experiment": data.get("experiment_name"),
        "window": [start, end],
        "segments": segment_metrics(segments),
        "terms": term_metrics(segments, checks or DEFAULT_TERM_CHECKS),
        "captions": caption_metrics(events),
        "caption_anomalies": caption_anomalies(caption_metrics(events)),
        "live_overlay": live_overlay_metrics(events),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiments", type=Path, nargs="+")
    parser.add_argument("--start", type=float, default=None)
    parser.add_argument("--end", type=float, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--fail-on-anomaly",
        action="store_true",
        help="exit 1 if any caption reading-speed anomaly is found",
    )
    args = parser.parse_args(argv)

    summaries = []
    for path in args.experiments:
        data = json.loads(path.read_text(encoding="utf-8"))
        summary = score(data, start=args.start, end=args.end)
        summary["path"] = str(path)
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.fail_on_anomaly and any(x["caption_anomalies"] for x in summaries):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
