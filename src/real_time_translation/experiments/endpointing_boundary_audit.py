"""Retroactive audit: does a shorter endpointing threshold commit mid-sentence?

causal-supervision2026-turn-aware-streaming-asr (see
research_agent/state/papers.json) argues silence-duration-only endpointing is
the wrong signal because within-turn pauses routinely exceed between-turn
gaps -- what actually distinguishes a turn boundary is whether the words
spoken so far form a semantically complete thought. This repo already has 6
endpointing-threshold sweep experiments on the exact same clip/time-range
(experiments/20260903_chunk_latency_sweep2_{300,500,800,1200,1500,2000}ms.json)
whose segment_count varies despite identical audio, but nothing yet checks
*where* those extra utterance splits land: at real sentence boundaries, or
mid-sentence.

This module computes, for each sweep experiment, the fraction of
utterance-end commits that land mid-sentence, using two independent signals:

1. asr_self_ends_sentence: does Deepgram's own committed text for that
   utterance already end in '.', '?' or '!'? (nova-3-general does emit
   punctuation, so this is a real signal, not just absent formatting.)
2. ground_truth_ends_sentence: does the matched span in the existing
   reference transcript (experiments/refs/wjZofJX0v4M_5_15_en.txt) show a
   sentence-ending mark right after the committed text's last few words?
   This catches cases where Deepgram's own punctuation is wrong (e.g. a
   missed "." on a genuinely complete sentence, or a spurious "." mid-clause).

Design notes / caveats (found while implementing, see
research_agent/state/hypotheses.json h-endpointing-pause-vs-sentence-
boundary-audit for the full history):

- `TimedEvent.is_utterance_end` defaults to `True` and is only ever
  explicitly set on translation_partial/translation_complete events (see
  video_segment.py on_result()) -- NOT on asr_interim events, which always
  carry the unpopulated default. So "is_utterance_end=True on an asr_interim
  event" means nothing; the real per-utterance-end signal lives on
  translation events.
- `TranslationResult.original_text` (the full accumulated source text) was
  only added to TimedEvent retroactively (2026-09-09) -- these sweep
  experiments predate that (2026-09-03) and have it empty. So the
  committed utterance's English text has to come from the asr_interim
  event stream instead, joined to the utterance-ending translation event by
  nearest `asr_end_time` (exact-key joins mostly miss -- confirmed by
  inspection, only ~3/16 exact matches on one file -- because the
  translation event's own `asr_end_time` and the closest asr_interim
  event's `asr_end_time` are recorded from different points in the
  pipeline and drift by up to ~1s). This is the same "no true utterance_id
  join" limitation already documented for h-cross-utterance-flicker and
  h-gemini-only-masking-replay.
"""

from __future__ import annotations

import argparse
import csv
import glob
import re
from dataclasses import dataclass
from pathlib import Path

SENTENCE_END_CHARS = (".", "?", "!")
# How far off (seconds) a translation event's asr_end_time may be from the
# nearest asr_interim event's asr_end_time before we give up trying to
# recover the committed English text for that utterance-end at all.
MATCH_TOLERANCE_SECONDS = 2.5
# How many trailing words of the committed text to locate in the ground
# truth transcript.
TRAILING_WORDS_FOR_GT_MATCH = 4


def _ends_with_sentence_punct(text: str) -> bool:
    stripped = text.rstrip().rstrip('"').rstrip("'").rstrip()
    return stripped.endswith(SENTENCE_END_CHARS)


@dataclass(frozen=True)
class UtteranceEndAudit:
    asr_end_time: float
    matched_interim_diff: float | None
    committed_text: str
    asr_self_ends_sentence: bool | None
    ground_truth_ends_sentence: bool | None


def _committed_utterance_ends(events: list[dict]) -> list[UtteranceEndAudit]:
    """One entry per translation event where is_utterance_end=True, i.e.
    every point this experiment run actually committed/finalized an
    utterance (as opposed to a soft-finalized mid-utterance chunk)."""
    asr_interims = [e for e in events if e.get("kind") == "asr_interim"]
    translation_ends = [
        e
        for e in events
        if e.get("kind") in ("translation_partial", "translation_complete")
        and e.get("is_utterance_end")
    ]

    results: list[UtteranceEndAudit] = []
    for e in translation_ends:
        target_end = e.get("asr_end_time")
        if target_end is None or not asr_interims:
            results.append(UtteranceEndAudit(0.0, None, "", None, None))
            continue
        best = min(asr_interims, key=lambda a: abs(a["asr_end_time"] - target_end))
        diff = abs(best["asr_end_time"] - target_end)
        if diff > MATCH_TOLERANCE_SECONDS:
            results.append(UtteranceEndAudit(target_end, diff, "", None, None))
            continue
        text = best.get("text", "")
        self_ends = _ends_with_sentence_punct(text) if text else None
        results.append(
            UtteranceEndAudit(
                asr_end_time=target_end,
                matched_interim_diff=diff,
                committed_text=text,
                asr_self_ends_sentence=self_ends,
                ground_truth_ends_sentence=None,  # filled in by caller
            )
        )
    return results


def _load_ground_truth_tokens(path: Path) -> list[tuple[str, bool]]:
    """Returns [(lowercased_word, sentence_ends_right_after), ...] preserving
    transcript order. `sentence_ends_right_after` is True when a '.', '?' or
    '!' token immediately follows that word in the raw transcript."""
    raw = path.read_text(encoding="utf-8")
    tokens = re.findall(r"[A-Za-z']+|[.?!]", raw)
    result: list[tuple[str, bool]] = []
    for i, tok in enumerate(tokens):
        if tok in SENTENCE_END_CHARS:
            continue
        ends_after = i + 1 < len(tokens) and tokens[i + 1] in SENTENCE_END_CHARS
        result.append((tok.lower(), ends_after))
    return result


def _check_ground_truth_boundary(
    committed_text: str, gt_tokens: list[tuple[str, bool]], search_from: int
) -> tuple[bool | None, int]:
    """Locates the committed text's trailing words in the ground-truth token
    list (searching forward from `search_from` to respect chronological
    order and avoid matching an earlier, unrelated repeated phrase), and
    reports whether a sentence-ending mark follows immediately after the
    match in the ground truth. Returns (result, new_search_from)."""
    words = re.findall(r"[A-Za-z']+", committed_text.lower())
    if not words:
        return None, search_from
    trailing = words[-TRAILING_WORDS_FOR_GT_MATCH:]
    n = len(trailing)
    gt_words = [w for w, _ in gt_tokens]
    for start in range(search_from, len(gt_words) - n + 1):
        if gt_words[start : start + n] == trailing:
            return gt_tokens[start + n - 1][1], start + n
    # Not found forward of search_from -- try a full-transcript search as a
    # fallback (e.g. ASR text diverges enough from the reference that our
    # naive pointer skipped past the real match); if still not found, give up.
    for start in range(0, len(gt_words) - n + 1):
        if gt_words[start : start + n] == trailing:
            return gt_tokens[start + n - 1][1], max(search_from, start + n)
    return None, search_from


@dataclass(frozen=True)
class ExperimentAudit:
    experiment_name: str
    json_path: str
    endpointing_threshold_ms: int | None
    num_utterance_ends: int
    num_matched_to_asr_text: int
    num_asr_self_mid_sentence: int
    num_matched_to_ground_truth: int
    num_ground_truth_mid_sentence: int

    @property
    def asr_self_mid_sentence_rate(self) -> float | None:
        if self.num_matched_to_asr_text == 0:
            return None
        return self.num_asr_self_mid_sentence / self.num_matched_to_asr_text

    @property
    def ground_truth_mid_sentence_rate(self) -> float | None:
        if self.num_matched_to_ground_truth == 0:
            return None
        return self.num_ground_truth_mid_sentence / self.num_matched_to_ground_truth


_THRESHOLD_RE = re.compile(r"_(\d+)ms\.json$")


def _parse_threshold_ms(path: Path) -> int | None:
    m = _THRESHOLD_RE.search(path.name)
    return int(m.group(1)) if m else None


def audit_experiment(
    path: Path, gt_tokens: list[tuple[str, bool]]
) -> ExperimentAudit:
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    ends = _committed_utterance_ends(events)

    search_from = 0
    num_matched_asr = 0
    num_asr_mid = 0
    num_matched_gt = 0
    num_gt_mid = 0
    for end in ends:
        if not end.committed_text:
            continue
        num_matched_asr += 1
        if end.asr_self_ends_sentence is False:
            num_asr_mid += 1
        gt_result, search_from = _check_ground_truth_boundary(
            end.committed_text, gt_tokens, search_from
        )
        if gt_result is not None:
            num_matched_gt += 1
            if not gt_result:
                num_gt_mid += 1

    return ExperimentAudit(
        experiment_name=data.get("experiment_name", path.stem),
        json_path=str(path),
        endpointing_threshold_ms=_parse_threshold_ms(path),
        num_utterance_ends=len(ends),
        num_matched_to_asr_text=num_matched_asr,
        num_asr_self_mid_sentence=num_asr_mid,
        num_matched_to_ground_truth=num_matched_gt,
        num_ground_truth_mid_sentence=num_gt_mid,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=None,
        help="Experiment JSON file(s) or glob(s). Defaults to the endpointing "
        "sweep files under experiments/20260903_chunk_latency_sweep2_*ms.json",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=Path("experiments/refs/wjZofJX0v4M_5_15_en.txt"),
        help="Reference transcript covering the sweep clip's time range.",
    )
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("experiments/endpointing_boundary_audit.csv"),
    )
    args = parser.parse_args()

    patterns = args.paths or ["experiments/20260903_chunk_latency_sweep2_*ms.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    gt_tokens = _load_ground_truth_tokens(args.ground_truth)

    audits = [audit_experiment(p, gt_tokens) for p in paths]
    audits.sort(key=lambda a: (a.endpointing_threshold_ms or 0))

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "endpointing_threshold_ms",
                "num_utterance_ends",
                "num_matched_to_asr_text",
                "num_asr_self_mid_sentence",
                "asr_self_mid_sentence_rate",
                "num_matched_to_ground_truth",
                "num_ground_truth_mid_sentence",
                "ground_truth_mid_sentence_rate",
            ]
        )
        for a in audits:
            writer.writerow(
                [
                    a.experiment_name,
                    a.json_path,
                    a.endpointing_threshold_ms,
                    a.num_utterance_ends,
                    a.num_matched_to_asr_text,
                    a.num_asr_self_mid_sentence,
                    a.asr_self_mid_sentence_rate,
                    a.num_matched_to_ground_truth,
                    a.num_ground_truth_mid_sentence,
                    a.ground_truth_mid_sentence_rate,
                ]
            )

    print(f"audited {len(audits)} experiment(s) -> {args.out_csv}")
    for a in audits:
        asr_rate = a.asr_self_mid_sentence_rate
        gt_rate = a.ground_truth_mid_sentence_rate
        asr_rate_str = "n/a" if asr_rate is None else f"{asr_rate:.3f}"
        gt_rate_str = "n/a" if gt_rate is None else f"{gt_rate:.3f}"
        print(
            f"  {a.endpointing_threshold_ms}ms: "
            f"{a.num_utterance_ends} utterance-ends, "
            f"asr-self mid-sentence rate={asr_rate_str} "
            f"({a.num_asr_self_mid_sentence}/{a.num_matched_to_asr_text}), "
            f"ground-truth mid-sentence rate={gt_rate_str} "
            f"({a.num_ground_truth_mid_sentence}/{a.num_matched_to_ground_truth})"
        )


if __name__ == "__main__":
    main()
