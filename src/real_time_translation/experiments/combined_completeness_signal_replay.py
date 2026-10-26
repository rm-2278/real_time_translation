"""h-combined-translate-completeness-signal: does folding the semantic-
completeness decision INTO the main translation call (one combined call)
produce the same completeness verdict as h-semantic-completeness-gating's
existing SEPARATE classifier call (check_completeness), on identical
growing ASR-interim text?

cuhksz2026-iwslt-qwen3omni-wait-token (IWSLT 2026) bakes its streaming
read/write decision directly into the model's own generation (wait tokens
emitted as part of the output) instead of using a separate policy module.
h-semantic-completeness-gating (cycle ~17) implemented a conceptually
adjacent idea here as a SEPARATE, non-translation classifier call
(LLMTranslator.check_completeness), and its own result_summary blamed that
second call's rate-limit contention with real translation calls for an
observed latency regression. This script tests whether the two signals
(separate classifier vs. a combined translate+completeness-tag call) agree
on the same text, as groundwork for whether the second call could be
removed.

IMPORTANT (corrected plan, see hypotheses.json's required_changes for the
full story of what changed from the original plan): h-semantic-completeness
-gating's own experiment JSONs do NOT log check_completeness's per-call
True/False decisions anywhere -- only growing asr_interim text snapshots are
recorded. This script therefore issues a FRESH separate-classifier call (not
a reuse of any historical decision, since none exists) alongside the new
combined call, for each sampled snapshot. Also: translate_stream has no
structured-output schema to extend (confirmed by reading llm_translator.py
directly) -- the "combined" call here uses a new ad-hoc
<completeness>COMPLETE|INCOMPLETE</completeness> delimiter tag appended
after the translation, invented for this experiment only, not a change to
production code.

Pure replay against gemini-3-flash-preview (the real translation model) +
gemini-3-flash-preview again for the classifier-equivalent call (same
model check_completeness itself would use in production, not the cheaper
judge model). Zero new Deepgram spend, zero live ASR -- reuses already-
recorded asr_interim snapshots from experiments/20260917_h_semantic_gating_
test_v2.json.

Usage:
  python3 -m real_time_translation.experiments.combined_completeness_signal_replay
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

SOURCE_JSON = Path("experiments/20260917_h_semantic_gating_test_v2.json")
OUT_JSON = Path("experiments/combined_completeness_signal_replay.json")

# Same wording as llm_translator.py's _COMPLETENESS_PROMPT_TEMPLATE --
# replicated here (not imported) since this is a standalone experiment
# call, not a production code path.
_SEPARATE_CLASSIFIER_PROMPT = (
    "The following is a live, possibly-incomplete transcript fragment "
    "of spoken English:\n\n"
    '"{text}"\n\n'
    "Does this fragment read as a grammatically and semantically "
    "COMPLETE clause or sentence (even if short), or does it clearly "
    "sound like it will continue (e.g. ends mid-phrase, on a "
    "conjunction, or on an obviously incomplete clause)?\n"
    "Answer with exactly one word: COMPLETE or INCOMPLETE."
)

_COMBINED_PROMPT = (
    "The following is a live, possibly-incomplete transcript fragment "
    "of spoken English:\n\n"
    '"{text}"\n\n'
    "Do two things in one response:\n"
    "1. Translate this fragment into natural Japanese, as a real-time "
    "subtitle would show it.\n"
    "2. Judge whether the fragment reads as a grammatically and "
    "semantically COMPLETE clause or sentence (even if short), or "
    "clearly sounds like it will continue.\n\n"
    "Respond in exactly this format, no markdown fences:\n"
    "<translation>...japanese text...</translation>\n"
    "<completeness>COMPLETE or INCOMPLETE</completeness>"
)

MODEL = "gemini-3-flash-preview"
SAMPLE_STRIDE = 3  # bound cost: ~91 asr_interim events / 3 ~= 30 samples


def _classify_separate(client, text: str) -> bool | None:
    prompt = _SEPARATE_CLASSIFIER_PROMPT.format(text=text)
    try:
        response = client.models.generate_content(model=MODEL, contents=prompt)
        answer = (response.text or "").strip().upper()
    except Exception:  # noqa: BLE001
        return None
    if "INCOMPLETE" in answer:
        return False
    if "COMPLETE" in answer:
        return True
    return None


def _classify_combined(client, text: str) -> tuple[str | None, bool | None]:
    prompt = _COMBINED_PROMPT.format(text=text)
    try:
        response = client.models.generate_content(model=MODEL, contents=prompt)
        raw = response.text or ""
    except Exception:  # noqa: BLE001
        return None, None
    translation_match = re.search(
        r"<translation>(.*?)</translation>", raw, re.DOTALL
    )
    completeness_match = re.search(
        r"<completeness>\s*(\w+)\s*</completeness>", raw, re.DOTALL
    )
    translation = translation_match.group(1).strip() if translation_match else None
    verdict: bool | None = None
    if completeness_match:
        tag = completeness_match.group(1).strip().upper()
        if "INCOMPLETE" in tag:
            verdict = False
        elif "COMPLETE" in tag:
            verdict = True
    return translation, verdict


def _sampled_snapshots(data: dict) -> list[dict]:
    events = data["results"]["events"]
    interims = [e for e in events if e.get("kind") == "asr_interim"]
    return interims[::SAMPLE_STRIDE]


def analyze(source_path: Path) -> dict:
    from dotenv import load_dotenv
    from google import genai

    load_dotenv(override=True)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    data = json.loads(source_path.read_text(encoding="utf-8"))
    snapshots = _sampled_snapshots(data)

    rows: list[dict] = []
    agree = 0
    disagree = 0
    either_none = 0

    for snap in snapshots:
        text = snap.get("text") or snap.get("original_text") or ""
        if not text.strip():
            continue
        separate_verdict = _classify_separate(client, text)
        combined_translation, combined_verdict = _classify_combined(client, text)

        if separate_verdict is None or combined_verdict is None:
            either_none += 1
            agreement = None
        elif separate_verdict == combined_verdict:
            agree += 1
            agreement = True
        else:
            disagree += 1
            agreement = False

        rows.append(
            {
                "text": text,
                "playback_offset": snap.get("playback_offset"),
                "separate_verdict": separate_verdict,
                "combined_verdict": combined_verdict,
                "combined_translation": combined_translation,
                "agreement": agreement,
            }
        )

    scored = agree + disagree
    agreement_rate = agree / scored if scored else None

    return {
        "source": str(source_path),
        "model": MODEL,
        "sample_stride": SAMPLE_STRIDE,
        "num_snapshots_sampled": len(snapshots),
        "num_rows_with_text": len(rows),
        "num_scored_pairs": scored,
        "num_either_none": either_none,
        "agree": agree,
        "disagree": disagree,
        "agreement_rate": agreement_rate,
        "rows": rows,
        "notes": (
            "Each sampled asr_interim growing-text snapshot gets TWO fresh calls: "
            "(1) the separate completeness classifier, replicating "
            "check_completeness's existing production prompt verbatim; (2) a new "
            "combined translate+completeness-tag call. agreement_rate is "
            "agree / (agree + disagree), excluding pairs where either call "
            "returned an unparseable/None verdict (counted separately in "
            "num_either_none). This tests ONLY whether the two signals agree on "
            "identical text -- it does NOT measure the actual latency or "
            "rate-limit-contention effect of removing the separate call in "
            "production (that would require a live A/B against pipeline.py's "
            "force-finalize path, out of this replay's $0.10 scope per "
            "hypotheses.json)."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    args = parser.parse_args()

    result = analyze(args.source)

    with args.out_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json}")


if __name__ == "__main__":
    main()
