"""Does an EN word-order "reordering burden" proxy correlate with MT
latency, retroactively over already-recorded experiment events? No new
ASR/LLM calls.

Motivation (h-enja-word-order-reordering-proxy-latency, cycle 64):
`doi2024-enja-si-word-order` documents that English-Japanese simultaneous
interpretation is unusually burdened by word-order divergence -- English is
SVO while Japanese is SOV, so the Japanese predicate/verb lands clause-
finally and an incremental interpreter often must wait for the English verb
before committing the Japanese sentence. This pipeline's LLM translator
commits whole utterances/batches rather than word-by-word, so if this effect
shows up here at all, it should appear as a correlation between how late the
English source's main verb appears and that utterance's MT latency, not as
an incremental-policy failure.

This module only looks at `is_utterance_end=True` `translation_complete`
events. Cycle 17's reflections.md finding (`original_text` on
`translation_complete` events is NOT reliably cumulative across *mid-
utterance* batch transitions, only 76% prefix-nested across the corpus)
does not apply here: `pipeline.py`'s utterance-end commit path (the
`is_utterance_end` branch around `_emit_batch_result`'s `acc_source`
handling) sets `original_text` to the utterance's full accumulated source
text exactly once, at the single event this module reads per utterance --
there is no mid-utterance prefix-nesting question to get wrong for that
event.

`_verb_position_ratio()` is a deliberately crude, dependency-free heuristic
(no spacy/nltk/etc. is in this repo's dependencies): it finds the first
token matching a small closed-class list of English auxiliary/modal/copula
forms (is/are/was/.../will/would/can/.../must/should/...), which in natural
spoken English usually marks the start of the finite verb phrase, and
reports its position as a 0-1 ratio of the sentence's token count. Returns
`None` (unresolved) when no such token is found -- e.g. simple-present
sentences with no auxiliary ("I like cats") -- rather than guessing, and
the resolution rate is reported plainly so this isn't mistaken for a
validated POS tagger.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from dataclasses import dataclass
from pathlib import Path

from real_time_translation.experiments.mt_latency_decomposition import (
    batch_latencies,
)
from real_time_translation.experiments.stats import mean, pearson_r

# Closed-class English auxiliary/modal/copula forms. Deliberately coarse --
# see module docstring. Checked against lowercased, punctuation-stripped
# tokens.
_AUX_VERBS = frozenset(
    {
        "is", "are", "was", "were", "am", "be", "been", "being",
        "do", "does", "did",
        "have", "has", "had",
        "will", "would", "can", "could", "should", "shall", "must",
        "might", "may", "need", "needs",
    }
)

_TOKEN_RE = re.compile(r"[A-Za-z']+")


def _verb_position_ratio(sentence: str) -> float | None:
    """0 (verb at the very start) to ~1 (verb at the very end) position of
    the first closed-class auxiliary/modal/copula token, normalized by
    token count. `None` if no such token is found or the sentence has no
    tokens."""
    tokens = _TOKEN_RE.findall(sentence.lower())
    if not tokens:
        return None
    for i, tok in enumerate(tokens):
        if tok in _AUX_VERBS:
            return i / len(tokens)
    return None


@dataclass(frozen=True)
class UtteranceDataPoint:
    experiment_name: str
    json_path: str
    key: tuple[float, float]
    token_count: int
    verb_position_ratio: float | None
    queue_wait_s: float | None
    stream_duration_s: float | None


def collect_experiment(path: Path) -> list[UtteranceDataPoint]:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    if not events:
        return []

    latencies = {b.key: b for b in batch_latencies(events)}
    experiment_name = data.get("experiment_name", path.stem)

    points: list[UtteranceDataPoint] = []
    for e in events:
        if e.get("kind") != "translation_complete" or not e.get(
            "is_utterance_end", True
        ):
            continue
        source = e.get("original_text") or ""
        tokens = _TOKEN_RE.findall(source.lower())
        if not tokens:
            continue
        key = (e.get("asr_start_time", 0.0), e.get("asr_end_time", 0.0))
        batch = latencies.get(key)
        points.append(
            UtteranceDataPoint(
                experiment_name=experiment_name,
                json_path=str(path),
                key=key,
                token_count=len(tokens),
                verb_position_ratio=_verb_position_ratio(source),
                queue_wait_s=batch.queue_wait_s if batch else None,
                stream_duration_s=batch.stream_duration_s if batch else None,
            )
        )
    return points


@dataclass(frozen=True)
class CorrelationSummary:
    num_utterances: int
    num_resolved_proxy: int
    resolution_rate: float | None
    queue_wait_pearson_r: float | None
    queue_wait_n: int
    stream_duration_pearson_r: float | None
    stream_duration_n: int


def _spearman_r(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation, implemented as Pearson over ranks (ties
    get the average rank) -- avoids adding a new stats.py dependency for a
    single retroactive script."""

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = avg_rank
            i = j + 1
        return out

    if len(xs) < 2:
        return None
    return pearson_r(ranks(xs), ranks(ys))


def summarize(points: list[UtteranceDataPoint]) -> CorrelationSummary:
    resolved = [p for p in points if p.verb_position_ratio is not None]
    qw_pairs = [
        (p.verb_position_ratio, p.queue_wait_s)
        for p in resolved
        if p.queue_wait_s is not None
    ]
    sd_pairs = [
        (p.verb_position_ratio, p.stream_duration_s)
        for p in resolved
        if p.stream_duration_s is not None
    ]
    return CorrelationSummary(
        num_utterances=len(points),
        num_resolved_proxy=len(resolved),
        resolution_rate=(len(resolved) / len(points)) if points else None,
        queue_wait_pearson_r=(
            pearson_r([x for x, _ in qw_pairs], [y for _, y in qw_pairs])
            if len(qw_pairs) >= 2
            else None
        ),
        queue_wait_n=len(qw_pairs),
        stream_duration_pearson_r=(
            pearson_r([x for x, _ in sd_pairs], [y for _, y in sd_pairs])
            if len(sd_pairs) >= 2
            else None
        ),
        stream_duration_n=len(sd_pairs),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=None)
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("experiments/word_order_reordering_proxy.csv"),
    )
    args = parser.parse_args()

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    all_points: list[UtteranceDataPoint] = []
    for path in paths:
        try:
            all_points.extend(collect_experiment(path))
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")
            continue

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "asr_start_time",
                "asr_end_time",
                "token_count",
                "verb_position_ratio",
                "queue_wait_s",
                "stream_duration_s",
            ]
        )
        for p in all_points:
            writer.writerow(
                [
                    p.experiment_name,
                    p.json_path,
                    p.key[0],
                    p.key[1],
                    p.token_count,
                    p.verb_position_ratio,
                    p.queue_wait_s,
                    p.stream_duration_s,
                ]
            )

    summary = summarize(all_points)
    qw_resolved = [
        p
        for p in all_points
        if p.verb_position_ratio is not None and p.queue_wait_s is not None
    ]
    sd_resolved = [
        p
        for p in all_points
        if p.verb_position_ratio is not None and p.stream_duration_s is not None
    ]
    spearman_qw = _spearman_r(
        [p.verb_position_ratio for p in qw_resolved],  # type: ignore[misc]
        [p.queue_wait_s for p in qw_resolved],  # type: ignore[misc]
    )
    spearman_sd = _spearman_r(
        [p.verb_position_ratio for p in sd_resolved],  # type: ignore[misc]
        [p.stream_duration_s for p in sd_resolved],  # type: ignore[misc]
    )

    print(f"analyzed {len(paths)} experiment file(s) -> {args.out_csv}")
    print(
        f"{summary.num_utterances} is_utterance_end=True utterances, "
        f"{summary.num_resolved_proxy} with a resolved verb_position_ratio "
        f"(resolution_rate={summary.resolution_rate})"
    )
    print(
        f"verb_position_ratio vs queue_wait_s: "
        f"pearson_r={summary.queue_wait_pearson_r} "
        f"spearman_r={spearman_qw} (n={summary.queue_wait_n})"
    )
    print(
        f"verb_position_ratio vs stream_duration_s: "
        f"pearson_r={summary.stream_duration_pearson_r} spearman_r={spearman_sd} "
        f"(n={summary.stream_duration_n})"
    )
    mean_ratio = mean(
        [p.verb_position_ratio for p in all_points if p.verb_position_ratio is not None]
    )
    print(f"mean verb_position_ratio: {mean_ratio}")


if __name__ == "__main__":
    main()
