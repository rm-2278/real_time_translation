"""LLM-judge scoring of translation information-retention vs. a source transcript.

chrF and readability_metrics.py both measure *form* (n-gram overlap, CPS
compliance) -- neither says whether a translation actually kept the
speaker's content. This is the missing axis for judging any technique that
deliberately trades literalness for brevity (e.g.
Config.reading_speed_budget_translation): a compressed translation can be
perfectly readable and still have quietly dropped the one number or term
that mattered.

Asks Gemini (a model *not* used for the translation being judged, to avoid
the judge grading its own homework when comparing Gemini-translated
variants) for a 0-100 retention score plus a short list of specific
dropped/distorted facts, given the English source and the Japanese
translation. This is a single holistic judgment call, not a ground-truth
metric -- report it as one data point, not a verdict.

CLI: real-time-translation-fidelity-judge --source <path|experiment.json>
     --translation <path|experiment.json> [--label NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


def _load_text(spec: str, *, field: str) -> str:
    """`spec` is either a plain text file or an experiment JSON (read `field`)."""
    path = Path(spec)
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["results"][field]
    return path.read_text(encoding="utf-8")


_JUDGE_PROMPT = """You are a strict bilingual (English/Japanese) fact-checker \
reviewing a real-time speech translation for information retention, not \
fluency. You will be shown the English source transcript (may contain ASR \
disfluencies -- ignore those) and its Japanese translation.

Score 0-100: what fraction of the source's substantive content (facts, \
numbers, technical terms, named entities, causal/logical relationships) \
survives in the translation, independent of how natural the Japanese \
reads. A translation that is short but keeps every key point should score \
high; a fluent translation that drops a number or a technical term should \
score lower.

Respond with ONLY a JSON object, no markdown fences:
{{"score": <int 0-100>, "dropped_or_distorted": ["short phrase", ...], \
"notes": "one sentence"}}

<source_english>
{source}
</source_english>

<translation_japanese>
{translation}
</translation_japanese>
"""


def judge(source: str, translation: str) -> dict:
    from dotenv import load_dotenv
    from google import genai

    load_dotenv(override=True)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    prompt = _JUDGE_PROMPT.format(source=source, translation=translation)
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=prompt,
    )
    text = response.text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Judge did not return JSON: {text[:300]!r}")
    return json.loads(match.group(0))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--translation", required=True, nargs="+")
    parser.add_argument("--label", nargs="+", default=None)
    args = parser.parse_args(argv)

    source = _load_text(args.source, field="full_asr")
    labels = args.label or [Path(t).stem for t in args.translation]

    for label, t_spec in zip(labels, args.translation, strict=True):
        translation = _load_text(t_spec, field="full_translation")
        result = judge(source, translation)
        dropped = result.get("dropped_or_distorted") or []
        print(f"{label}: retention_score={result.get('score')}")
        print(f"  notes: {result.get('notes')}")
        if dropped:
            print(f"  dropped/distorted ({len(dropped)}): {'; '.join(dropped)}")
        print()


if __name__ == "__main__":
    main()
