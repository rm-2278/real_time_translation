"""h-soft-anchor-gate-min3-erasure-decomposition: decompose the guest-talk
NE regression from h-soft-anchor-gate-min-words-3 into per-batch-transition
erasure.

h-soft-anchor-gate-min-words-3's own result_summary (cycle 23) found that on
the guest-talk clip, `gated_soft_anchor`'s aggregate mean_ne (0.377) was
markedly worse than `soft_anchor_replay`'s (0.089) -- the opposite of the
intended direction -- and recommended a focused follow-up decomposing NE on
gated vs. non-gated batches before drawing any conclusion. A span-level
recheck (described in this hypothesis's own text, verified directly against
the source JSON before writing it) already confirmed the regression is
concentrated in the 42 spans where the gate actually fired, not spread
uniformly across all 125 -- but that still leaves the finer, *within-span*
question open: inside those 42 gated spans, is the extra erasure confined to
the transition(s) the gate directly holds back, or does it also destabilize
later, non-gated transitions in the same span (a knock-on effect)?

This reuses `flicker_metrics._longest_common_prefix_len` directly (no
reimplementation) to compute per-transition erasure:
    erasure_t = len(outs[t]) - lcp(outs[t], outs[t+1])   for t = 0..n-2
on each repeat's `condition_repeats[cond][repeat]` (a per-batch cumulative
text list), for the guest-talk source's 42 spans with non-empty
`gated_batch_indices`.

Transition t (the transition INTO batch t+1) is labeled `gated_transition`
if (t+1) is in that span's `gated_batch_indices`, else
`nongated_transition_in_gated_span`. Both the `gated_soft_anchor` and
`soft_anchor_replay` conditions are compared per label, per the input JSON's
own two competing predictions ("contained re-reveal" vs "destabilization").

Zero new API calls -- pure retroactive analysis over an already-recorded
experiment JSON.

Usage:
  python3 -m real_time_translation.experiments.soft_anchor_gate_erasure_decomposition
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.experiments.flicker_metrics import (
    _longest_common_prefix_len,
)

SOURCE_JSON = Path("experiments/20260922_h_soft_anchor_gate_min3_replay.json")
GUEST_TALK_SOURCE = "20260915_llm_course_ep8_guest_talk_10min_rpm60.json"
OUT_JSON = Path("experiments/soft_anchor_gate_erasure_decomposition.json")
CONDITIONS = ("gated_soft_anchor", "soft_anchor_replay")


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass(frozen=True)
class Transition:
    span_index: int
    repeat: int
    transition_index: int  # t, the transition INTO batch t+1
    label: str  # "gated_transition" | "nongated_transition_in_gated_span"
    erasure_chars: int
    final_text_len: int
    erasure_normalized: float


def _per_transition_erasure(outs: list[str]) -> list[tuple[int, int]]:
    """[(erasure_chars, final_text_len)] for t=0..len(outs)-2, normalized
    against the REPEAT's own final (last-batch) text length, consistent
    with normalized_erasure()'s own per-span denominator."""
    if len(outs) < 2:
        return []
    final_len = len(outs[-1])
    result = []
    for t in range(len(outs) - 1):
        lcp = _longest_common_prefix_len(outs[t], outs[t + 1])
        erasure = len(outs[t]) - lcp
        result.append((erasure, final_len))
    return result


def _label(t: int, gated_batch_indices: list[int]) -> str:
    return "gated_transition" if (t + 1) in gated_batch_indices else (
        "nongated_transition_in_gated_span"
    )


def analyze() -> dict:
    data = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    by_source = data["results"]["by_source"]
    spans = by_source[GUEST_TALK_SOURCE]["spans"]
    gated_spans = [s for s in spans if s["gated_batch_indices"]]

    tagged: list[tuple[str, Transition]] = []
    for span in gated_spans:
        gated_indices = span["gated_batch_indices"]
        for condition in CONDITIONS:
            repeats = span["condition_repeats"][condition]
            for repeat_idx, outs in enumerate(repeats):
                if not outs:
                    continue
                for t, (erasure, final_len) in enumerate(
                    _per_transition_erasure(outs)
                ):
                    if final_len == 0:
                        continue
                    tagged.append(
                        (
                            condition,
                            Transition(
                                span_index=span["span_index"],
                                repeat=repeat_idx,
                                transition_index=t,
                                label=_label(t, gated_indices),
                                erasure_chars=erasure,
                                final_text_len=final_len,
                                erasure_normalized=erasure / final_len,
                            ),
                        )
                    )

    summary: dict[str, dict] = {}
    for condition in CONDITIONS:
        summary[condition] = {}
        for label in ("gated_transition", "nongated_transition_in_gated_span"):
            group = [tr for c, tr in tagged if c == condition and tr.label == label]
            raw = [tr.erasure_chars for tr in group]
            norm = [tr.erasure_normalized for tr in group]
            summary[condition][label] = {
                "n": len(group),
                "mean_erasure_chars": _mean(raw),
                "mean_erasure_normalized": _mean(norm),
            }

    # Per-span detail: excess erasure of gated_soft_anchor over
    # soft_anchor_replay, restricted to nongated_transition_in_gated_span
    # (the "does the gate's cost leak beyond the held-back batch itself"
    # question), averaged across repeats per span, sorted descending.
    per_span_excess: list[dict] = []
    for span in gated_spans:
        idx = span["span_index"]
        gated_vals = [
            tr.erasure_normalized
            for c, tr in tagged
            if c == "gated_soft_anchor"
            and tr.span_index == idx
            and tr.label == "nongated_transition_in_gated_span"
        ]
        replay_vals = [
            tr.erasure_normalized
            for c, tr in tagged
            if c == "soft_anchor_replay"
            and tr.span_index == idx
            and tr.label == "nongated_transition_in_gated_span"
        ]
        if not gated_vals or not replay_vals:
            continue
        excess = _mean(gated_vals) - _mean(replay_vals)
        per_span_excess.append(
            {
                "span_index": idx,
                "num_nongated_transitions_in_span": len(gated_vals),
                "gated_soft_anchor_mean_erasure_normalized": _mean(gated_vals),
                "soft_anchor_replay_mean_erasure_normalized": _mean(replay_vals),
                "excess_erasure_normalized": excess,
            }
        )
    per_span_excess.sort(key=lambda r: r["excess_erasure_normalized"], reverse=True)

    gated_label_summary = summary["gated_soft_anchor"]["gated_transition"]
    gated_replay_summary = summary["soft_anchor_replay"]["gated_transition"]
    nongated_label_summary = summary["gated_soft_anchor"][
        "nongated_transition_in_gated_span"
    ]
    nongated_replay_summary = summary["soft_anchor_replay"][
        "nongated_transition_in_gated_span"
    ]

    return {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": "h_soft_anchor_gate_erasure_decomposition",
        "domain": None,
        "input": {
            "type": "retroactive_analysis",
            "source_json": str(SOURCE_JSON),
            "source_key": GUEST_TALK_SOURCE,
            "num_gated_spans": len(gated_spans),
            "note": (
                "Follow-up to h-soft-anchor-gate-min-words-3's own "
                "result_summary, which flagged the guest-talk aggregate NE "
                "regression (gated_soft_anchor 0.377 vs soft_anchor_replay "
                "0.089) as unexplained and recommended this exact "
                "decomposition. Zero new Deepgram/LLM API calls -- reuses "
                "condition_repeats text already recorded in the source JSON."
            ),
        },
        "results": {
            "by_label_and_condition": summary,
            "per_span_excess_on_nongated_transitions": per_span_excess,
            "top_outlier_spans": per_span_excess[:5],
        },
        "conclusion": {
            "gated_transition_excess_erasure_normalized": (
                gated_label_summary["mean_erasure_normalized"]
                - gated_replay_summary["mean_erasure_normalized"]
                if gated_label_summary["mean_erasure_normalized"] is not None
                and gated_replay_summary["mean_erasure_normalized"] is not None
                else None
            ),
            "nongated_transition_excess_erasure_normalized": (
                nongated_label_summary["mean_erasure_normalized"]
                - nongated_replay_summary["mean_erasure_normalized"]
                if nongated_label_summary["mean_erasure_normalized"] is not None
                and nongated_replay_summary["mean_erasure_normalized"] is not None
                else None
            ),
        },
        "notes": (
            "h-soft-anchor-gate-min3-erasure-decomposition. See 'conclusion' "
            "for the two headline numbers: excess normalized erasure (gated_"
            "soft_anchor minus soft_anchor_replay) on gated transitions "
            "themselves vs. on non-gated transitions within the same gated "
            "spans. A small/near-zero nongated excess supports 'contained "
            "re-reveal' (prediction 1 in the hypothesis); a comparably large "
            "nongated excess supports 'destabilization' (prediction 2)."
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    out = analyze()
    OUT_JSON.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {OUT_JSON}")
    summary = out["results"]["by_label_and_condition"]
    for condition, labels in summary.items():
        for label, stats in labels.items():
            print(
                f"  {condition} / {label}: n={stats['n']} "
                f"mean_erasure_chars={stats['mean_erasure_chars']} "
                f"mean_erasure_normalized={stats['mean_erasure_normalized']}"
            )
    print("\nconclusion:", json.dumps(out["conclusion"], indent=2))
    print("\ntop outlier spans (by excess nongated erasure):")
    for row in out["results"]["top_outlier_spans"]:
        print(" ", row)


if __name__ == "__main__":
    main()
