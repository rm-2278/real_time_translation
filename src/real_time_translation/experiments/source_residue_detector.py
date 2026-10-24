"""h-translation-source-residue-detector: a cheap, judge()-free check for
untranslated English source text left behind in committed JA translation
output.

failure-aware-recoverable-llm-translation-2026 (research_agent/state/
papers.json, cycle 73 literature) describes a production LLM-translation
recovery protocol that includes a "source-language residue" check: flagging
cases where the model echoes source-language text instead of translating
it. No equivalent check exists anywhere in this repo's production code
(llm_translator.py, pipeline.py) or in any prior hypothesis/experiment --
this is distinct from h-soft-anchor-truncation-detector (cycle 71), which
flags outputs that are too SHORT relative to a sibling repeat; residue is
the opposite failure shape, an output of plausible length that contains a
verbatim chunk of the English source instead of its translation.

Confound checked before writing this (dictionary.csv): 46 entries have an
intentionally-English (or English-containing) target_term, up to 35
characters and multi-word (e.g. "Rotary Positional Embeddings (RoPE)",
"Chain of Thought"). A naive "flag any long run of Latin letters" detector
would false-positive heavily on these legitimate, intentional terms, so any
flagged ASCII run that exactly matches, or is a substring of, a
dictionary.csv target_term is excluded before anything is flagged.

The remaining candidate runs are only counted as true residue when they
also appear verbatim in that SAME event's own `original_text` field --
i.e. a same-event text/original_text field pairing, never a cross-event or
cross-batch comparison (deliberately avoiding the cycle-17 caveat in
PLAYBOOK.md about `original_text` not being reliably cumulative *across*
batches on translation_complete events -- this script never compares
original_text between two different events).

Only `kind == "translation_complete"` events are scanned (the committed
output a viewer actually sees), not `translation_partial` interim text.

Pure Python over experiment JSONs already on disk -- no live Deepgram/
Gemini/OpenAI call of any kind, no results.csv row (same retroactive-
analysis precedent as flicker_metrics.py / soft_anchor_truncation_detector.py).

Usage:
  python3 -m real_time_translation.experiments.source_residue_detector
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from pathlib import Path

DICTIONARY_PATH = Path("dictionary.csv")
MIN_RUN_LEN = 4
# A run below this length is far more likely to be an incidental short
# word/initialism (e.g. a one-off "a" or "ok") than genuine source residue.
_ASCII_RUN_RE = re.compile(r"[A-Za-z][A-Za-z '\-]*[A-Za-z]|[A-Za-z]")


def _load_dictionary_target_terms(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as f:
        return {row["target_term"].strip() for row in csv.DictReader(f)}


def _ascii_runs(text: str) -> list[str]:
    return [
        run.strip()
        for run in _ASCII_RUN_RE.findall(text)
        if len(run.strip()) >= MIN_RUN_LEN
    ]


def _is_dictionary_term(run: str, dictionary_terms: set[str]) -> bool:
    return any(run == term or run in term for term in dictionary_terms)


def _find_residue(
    text: str, original_text: str, dictionary_terms: set[str]
) -> list[str]:
    if not text or not original_text:
        return []
    flagged = []
    for run in _ascii_runs(text):
        if _is_dictionary_term(run, dictionary_terms):
            continue
        if run in original_text:
            flagged.append(run)
    return flagged


def analyze_experiment(path: Path, dictionary_terms: set[str]) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    flagged_events = []
    num_checked = 0
    for event in events:
        if event.get("kind") != "translation_complete":
            continue
        text = event.get("text", "")
        original_text = event.get("original_text", "")
        if not text or not original_text:
            continue
        num_checked += 1
        residue = _find_residue(text, original_text, dictionary_terms)
        if residue:
            flagged_events.append(
                {
                    "original_text": original_text,
                    "text": text,
                    "flagged_runs": residue,
                }
            )
    return {
        "experiment_name": data.get("experiment_name", path.stem),
        "json_path": str(path),
        "num_translation_complete_checked": num_checked,
        "num_flagged": len(flagged_events),
        "flagged_events": flagged_events,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=None,
        help="Experiment JSON file(s) or glob(s). Defaults to experiments/*.json",
    )
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("experiments/source_residue_detector.csv"),
        help="Sidecar CSV to write (kept separate from results.csv, same "
        "convention as flicker_metrics.py)",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=Path("experiments/20261024_h_translation_source_residue_detector.json"),
        help="Full detail JSON (flagged events with example text)",
    )
    args = parser.parse_args()

    dictionary_terms = _load_dictionary_target_terms(DICTIONARY_PATH)

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        matched = sorted(glob.glob(pattern))
        paths.extend(Path(p) for p in matched)
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    reports: list[dict] = []
    for path in paths:
        try:
            report = analyze_experiment(path, dictionary_terms)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")
            continue
        if report["num_translation_complete_checked"] == 0:
            continue
        reports.append(report)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "num_translation_complete_checked",
                "num_flagged",
            ]
        )
        for r in reports:
            writer.writerow(
                [
                    r["experiment_name"],
                    r["json_path"],
                    r["num_translation_complete_checked"],
                    r["num_flagged"],
                ]
            )

    total_checked = sum(r["num_translation_complete_checked"] for r in reports)
    total_flagged = sum(r["num_flagged"] for r in reports)
    output = {
        "hypothesis_id": "h-translation-source-residue-detector",
        "dictionary_terms_loaded": len(dictionary_terms),
        "num_experiments_scanned": len(reports),
        "total_translation_complete_checked": total_checked,
        "total_flagged": total_flagged,
        "reports": [r for r in reports if r["num_flagged"] > 0],
    }
    args.out_json.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"Loaded {len(dictionary_terms)} dictionary target_terms")
    print(
        f"Scanned {len(reports)} experiments, "
        f"{total_checked} translation_complete events"
    )
    print(f"Total flagged: {total_flagged}")
    for r in reports:
        if r["num_flagged"]:
            print(f"  {r['experiment_name']}: {r['num_flagged']} flagged")
            for ev in r["flagged_events"][:3]:
                print(f"    runs={ev['flagged_runs']} text={ev['text']!r}")
    print(f"\nWrote {args.out_csv} and {args.out_json}")


if __name__ == "__main__":
    main()
