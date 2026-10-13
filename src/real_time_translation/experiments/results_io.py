"""Reading and writing the experiment artifacts under `experiments/`.

The runners write `experiments/YYYYMMDD_<name>.json` plus one row in
`experiments/results.csv` (see CLAUDE.md "Experiment Logging Rules"); the
analyzers read those JSON files back. Anything about that on-disk format
lives here so it is defined once.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path


def slugify(value: str) -> str:
    """Lowercase, `[a-z0-9_]` only; used to build experiment file names."""
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "experiment"


def ensure_csv_header(csv_path: Path, header: list[str]) -> None:
    """Migrate an existing results.csv to a new (superset) header in place.

    Adding metric columns over time (chrF, glossary adherence, ...) means
    the header on disk can lag `header` here. Rewriting preserves old rows
    (missing new columns read back as "") instead of producing a file whose
    physical header no longer matches later-appended rows' column order.
    """
    if not csv_path.exists():
        return

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_header = reader.fieldnames
        if existing_header is None or list(existing_header) == header:
            return
        rows = list(reader)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in header})


def maybe_compute_chrf(
    *,
    hypothesis: str,
    reference_text_path: Path | None,
) -> float | None:
    """chrF of `hypothesis` against a reference file, or None if no reference."""
    if reference_text_path is None:
        return None

    reference = reference_text_path.read_text(encoding="utf-8").strip()
    if not reference:
        return None

    try:
        from sacrebleu.metrics import CHRF  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "sacrebleu is not installed. Run: uv sync --extra experiments"
        ) from exc

    metric = CHRF(word_order=2)
    return float(metric.corpus_score([hypothesis], [[reference]]).score)


def last_cumulative_text(repeat: list[str]) -> str:
    """Final text of one repeated run, whose entries are cumulative snapshots."""
    return repeat[-1] if repeat else ""
