"""h-dictionary-term-compliance-leakage: does the LLM actually use the
Japanese target_term dictionary.csv specifies, or does the literal English
source_term sometimes leak through untranslated instead?

Direct follow-up of h-translation-source-residue-detector (cycle 73),
triggered by re-reading that script's own output JSON and finding that its
word-count filtering (<=4 words assumed benign code-switching) mislabels
a different failure shape: "language model" -> "言語モデル" is a genuine,
unambiguous dictionary.csv mapping (not one of the 46 intentional-English-
anchor entries like RoPE/Chain of Thought), yet the English source string
appeared verbatim in committed output where 言語モデル should have been
("language modelの効率性について...").

This script is narrower and more targeted than source_residue_detector.py
(which flags *any* unexplained ASCII run) or glossary_metrics.py /
technical_term_recall.py (which score aggregate adherence/recall rates, not
per-occurrence leaked-vs-compliant classification with example triples):
for each dictionary.csv entry whose target_term is actually Japanese, every
time that entry's source_term appears (case-insensitive, word-boundary) in
a translation_complete event's own original_text, classify that SAME
event's text field as:
  - "compliant": the Japanese target_term appears in text
  - "leaked": the literal English source_term appears verbatim in text
    instead (case-insensitive)
  - "ambiguous": neither appears (the sentence was reworded enough that
    neither string is literally present)

Same same-event original_text/text pairing as source_residue_detector.py
(never cross-event -- see PLAYBOOK.md's cycle-17 caveat about
original_text not being reliably cumulative across batches).

Pure Python over experiment JSONs + dictionary.csv already on disk -- no
live Deepgram/Gemini/OpenAI call of any kind, no results.csv row (same
retroactive-analysis precedent as flicker_metrics.py /
source_residue_detector.py).

Usage:
  python3 -m real_time_translation.experiments.dictionary_compliance_checker
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from pathlib import Path

DICTIONARY_PATH = Path("dictionary.csv")
_JAPANESE_CHAR_RE = re.compile(r"[぀-ヿ一-鿿]")


def _is_japanese(target_term: str) -> bool:
    return bool(_JAPANESE_CHAR_RE.search(target_term))


def _load_translated_entries(path: Path) -> list[dict]:
    """dictionary.csv rows whose target_term contains at least one
    Japanese character -- excludes the intentional-English-anchor entries
    (e.g. RoPE, Chain of Thought), which are correctly-English by design
    and out of scope for this check.
    """
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [
        {
            "source_term": r["source_term"].strip(),
            "target_term": r["target_term"].strip(),
        }
        for r in rows
        if _is_japanese(r["target_term"])
    ]


def _source_term_pattern(source_term: str) -> re.Pattern:
    # Python's \w (and therefore \b) treats Japanese kana/kanji as word
    # characters too, so a plain \b...\b pattern finds no boundary at an
    # English/Japanese seam with no space (e.g. "attentionを" has no \b
    # between "n" and "を") -- verified empirically before relying on it:
    # this silently misclassified several genuine "leaked" occurrences as
    # "ambiguous" during initial development of this script. Use explicit
    # ASCII-only lookaround instead so a leaked term immediately followed
    # or preceded by Japanese text is still detected as a boundary.
    return re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(source_term) + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def analyze_experiment(path: Path, entries: list[dict]) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    per_entry_occurrences: dict[str, list[dict]] = {
        e["source_term"]: [] for e in entries
    }
    patterns = {
        e["source_term"]: _source_term_pattern(e["source_term"]) for e in entries
    }

    for event in events:
        if event.get("kind") != "translation_complete":
            continue
        text = event.get("text", "")
        original_text = event.get("original_text", "")
        if not text or not original_text:
            continue
        for entry in entries:
            source_term = entry["source_term"]
            target_term = entry["target_term"]
            if not patterns[source_term].search(original_text):
                continue
            if target_term in text:
                classification = "compliant"
            elif patterns[source_term].search(text):
                classification = "leaked"
            else:
                classification = "ambiguous"
            per_entry_occurrences[source_term].append(
                {
                    "classification": classification,
                    "original_text": original_text,
                    "text": text,
                }
            )

    return {
        "experiment_name": data.get("experiment_name", path.stem),
        "json_path": str(path),
        "per_entry_occurrences": per_entry_occurrences,
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
        default=Path("experiments/dictionary_compliance_checker.csv"),
        help="Per-entry compliance-rate sidecar CSV (kept separate from "
        "results.csv, same convention as flicker_metrics.py)",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=Path(
            "experiments/20261025_h_dictionary_term_compliance_leakage.json"
        ),
        help="Full detail JSON (per-entry occurrence counts + leaked example triples)",
    )
    args = parser.parse_args()

    entries = _load_translated_entries(DICTIONARY_PATH)

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        matched = sorted(glob.glob(pattern))
        paths.extend(Path(p) for p in matched)
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    # Aggregate across all experiments, per dictionary entry.
    aggregate: dict[str, dict] = {
        e["source_term"]: {"target_term": e["target_term"], "occurrences": []}
        for e in entries
    }
    num_experiments_scanned = 0
    for path in paths:
        try:
            report = analyze_experiment(path, entries)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")
            continue
        any_occurrence = any(report["per_entry_occurrences"].values())
        if not any_occurrence:
            continue
        num_experiments_scanned += 1
        for source_term, occurrences in report["per_entry_occurrences"].items():
            for occ in occurrences:
                aggregate[source_term]["occurrences"].append(
                    {**occ, "experiment_name": report["experiment_name"]}
                )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "source_term",
                "target_term",
                "num_occurrences",
                "num_compliant",
                "num_leaked",
                "num_ambiguous",
                "compliance_rate",
            ]
        )
        for source_term, data in aggregate.items():
            occs = data["occurrences"]
            if not occs:
                continue
            n_compliant = sum(1 for o in occs if o["classification"] == "compliant")
            n_leaked = sum(1 for o in occs if o["classification"] == "leaked")
            n_ambiguous = sum(1 for o in occs if o["classification"] == "ambiguous")
            denom = n_compliant + n_leaked
            compliance_rate = (n_compliant / denom) if denom else None
            writer.writerow(
                [
                    source_term,
                    data["target_term"],
                    len(occs),
                    n_compliant,
                    n_leaked,
                    n_ambiguous,
                    f"{compliance_rate:.4f}" if compliance_rate is not None else "",
                ]
            )

    all_occurrences = [o for d in aggregate.values() for o in d["occurrences"]]
    total_compliant = sum(
        1 for o in all_occurrences if o["classification"] == "compliant"
    )
    total_leaked = sum(1 for o in all_occurrences if o["classification"] == "leaked")
    total_ambiguous = sum(
        1 for o in all_occurrences if o["classification"] == "ambiguous"
    )

    leaked_examples = [
        {
            "source_term": source_term,
            "target_term": data["target_term"],
            "experiment_name": o["experiment_name"],
            "original_text": o["original_text"],
            "text": o["text"],
        }
        for source_term, data in aggregate.items()
        for o in data["occurrences"]
        if o["classification"] == "leaked"
    ]

    output = {
        "hypothesis_id": "h-dictionary-term-compliance-leakage",
        "num_translated_dictionary_entries": len(entries),
        "num_experiments_scanned": num_experiments_scanned,
        "total_occurrences": len(all_occurrences),
        "total_compliant": total_compliant,
        "total_leaked": total_leaked,
        "total_ambiguous": total_ambiguous,
        "overall_compliance_rate": (
            total_compliant / (total_compliant + total_leaked)
            if (total_compliant + total_leaked)
            else None
        ),
        "per_entry": [
            {
                "source_term": source_term,
                "target_term": data["target_term"],
                "num_occurrences": len(data["occurrences"]),
                "num_compliant": sum(
                    1 for o in data["occurrences"] if o["classification"] == "compliant"
                ),
                "num_leaked": sum(
                    1 for o in data["occurrences"] if o["classification"] == "leaked"
                ),
                "num_ambiguous": sum(
                    1 for o in data["occurrences"] if o["classification"] == "ambiguous"
                ),
            }
            for source_term, data in aggregate.items()
            if data["occurrences"]
        ],
        "leaked_examples": leaked_examples,
    }
    args.out_json.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"Loaded {len(entries)} translated (Japanese-target) dictionary entries")
    print(f"Scanned {num_experiments_scanned} experiments with at least one occurrence")
    print(
        f"Total occurrences: {len(all_occurrences)} "
        f"(compliant={total_compliant}, leaked={total_leaked}, "
        f"ambiguous={total_ambiguous})"
    )
    if total_compliant + total_leaked:
        rate = total_compliant / (total_compliant + total_leaked)
        print(f"Overall compliance rate (compliant / (compliant+leaked)): {rate:.4f}")
    for entry in output["per_entry"]:
        if entry["num_leaked"]:
            print(
                f"  {entry['source_term']} -> {entry['target_term']}: "
                f"{entry['num_leaked']} leaked / {entry['num_occurrences']} occurrences"
            )
    print(f"\nWrote {args.out_csv} and {args.out_json}")


if __name__ == "__main__":
    main()
