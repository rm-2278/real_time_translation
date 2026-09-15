"""Quantify ASR technical-term recognition against a curated glossary.

Two numbers per experiment JSON, both computed against a ground-truth
transcript (not the ASR's own output, so this isn't circular):

- `wer`: standard word-level Levenshtein WER over the whole transcript.
- `term_recall`: of the glossary terms that actually appear in the ground
  truth for this window, what fraction also appear (case-insensitively,
  substring match) in the ASR's own output. This isolates exactly what
  domain-glossary/keyterm-prompting features are supposed to help with --
  general WER can look fine while specific jargon still gets mangled.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path


def clean_asr_transcript(events: list[dict]) -> str | None:
    """A deduplicated, per-utterance ASR (source-language) transcript.

    `results.full_asr`/`segments[].asr` are naive concatenations of
    per-batch fragments and heavily overlap/duplicate each other (each
    asr_interim event's `text` is the CUMULATIVE growing hypothesis for the
    current utterance, not a delta) -- unusable for WER directly.

    The reliable source is each translation event's `original_text` (the
    English source text fed to that translation call), chained by
    `is_utterance_end` -- which, unlike asr_interim events' own
    `is_utterance_end`, this repo's research pipeline confirmed IS
    populated correctly on translation events (research_agent cycle 13).
    Keep only each utterance's LAST batch (fullest accumulated text).

    Returns None if the experiment predates `original_text` being recorded
    (added 2026-09-09) -- every value would silently be "", which would
    read as 0% WER/100% missing terms rather than "not measurable here".
    """
    batches: list[tuple[str, bool]] = []
    cur_key: tuple[float, float] | None = None
    cur_text = ""
    cur_ended = True
    saw_original_text_field = False

    for e in events:
        if e.get("kind") not in ("translation_partial", "translation_complete"):
            continue
        if "original_text" in e:
            saw_original_text_field = True
        key = (e.get("asr_start_time", 0.0), e.get("asr_end_time", 0.0))
        if cur_key is None or key != cur_key:
            if cur_key is not None:
                batches.append((cur_text, cur_ended))
            cur_key = key
        cur_text = e.get("original_text", "")
        cur_ended = e.get("is_utterance_end", True)
    if cur_key is not None:
        batches.append((cur_text, cur_ended))

    if not saw_original_text_field:
        return None

    out: list[str] = []
    last_text = ""
    for text, ended in batches:
        last_text = text
        if ended:
            out.append(last_text)
    if batches and not batches[-1][1]:
        out.append(last_text)
    return " ".join(out)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text.lower())


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Standard word-level Levenshtein WER: edits / len(reference)."""
    ref = _tokenize(reference)
    hyp = _tokenize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0

    # DP edit distance, O(len(ref)*len(hyp)) -- fine at these clip lengths.
    prev = list(range(len(hyp) + 1))
    for i in range(1, len(ref) + 1):
        curr = [i] + [0] * len(hyp)
        for j in range(1, len(hyp) + 1):
            if ref[i - 1] == hyp[j - 1]:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev = curr
    return prev[len(hyp)] / len(ref)


def load_glossary_terms(csv_path: Path) -> list[str]:
    with csv_path.open(encoding="utf-8") as f:
        return [row["source_term"] for row in csv.DictReader(f)]


def terms_present(text: str, terms: list[str]) -> set[str]:
    lowered = text.lower()
    return {t for t in terms if t.lower() in lowered}


@dataclass(frozen=True)
class TermRecallReport:
    wer: float
    terms_in_ground_truth: int
    terms_recognized: int
    term_recall: float
    missed_terms: list[str]


def analyze(
    ground_truth: str, asr_text: str, glossary_terms: list[str]
) -> TermRecallReport:
    gt_terms = terms_present(ground_truth, glossary_terms)
    found_terms = terms_present(asr_text, glossary_terms) & gt_terms
    missed = sorted(gt_terms - found_terms)
    recall = len(found_terms) / len(gt_terms) if gt_terms else float("nan")
    return TermRecallReport(
        wer=word_error_rate(ground_truth, asr_text),
        terms_in_ground_truth=len(gt_terms),
        terms_recognized=len(found_terms),
        term_recall=recall,
        missed_terms=missed,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--glossary", required=True, type=Path)
    parser.add_argument("--experiment", required=True, type=Path, nargs="+")
    args = parser.parse_args(argv)

    ground_truth = args.ground_truth.read_text(encoding="utf-8")
    terms = load_glossary_terms(args.glossary)

    for exp_path in args.experiment:
        data = json.loads(exp_path.read_text(encoding="utf-8"))
        asr_text = clean_asr_transcript(data["results"]["events"])
        if asr_text is None:
            print(
                f"{exp_path.name}: SKIPPED -- predates original_text "
                "(2026-09-09), no reliable clean transcript available"
            )
            continue
        report = analyze(ground_truth, asr_text, terms)
        print(
            f"{exp_path.name}: WER={report.wer:.1%} "
            f"term_recall={report.term_recall:.1%} "
            f"({report.terms_recognized}/{report.terms_in_ground_truth}) "
            f"missed={report.missed_terms}"
        )


if __name__ == "__main__":
    main()
