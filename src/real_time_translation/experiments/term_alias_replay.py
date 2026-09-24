"""A/B replay: do ASR aliases + the ASR-error prompt exception fix misheard terms?

2026-09-24 human review of the full LLM2024_8_part1 captions: "Josh Angou"
(Jiaxian Guo), "Muscle Lab" (Matsuo Lab), "AIME agents" (AI agents),
"suspension agent" (Suspicion-Agent), ... all went through translation
unchanged. Two causes:

1. The 114-entry session dictionary is in dynamic mode (> 80 entries), so
   only entries whose source term literally appears in the ASR text are
   injected -- a misheard name never retrieves its own entry.
2. Prompt rule 4 told the model to keep proper nouns "EXACTLY as they
   appear", which forbids exactly the correction needed.

This re-translates the reported segments (plus controls that should NOT
change, e.g. correct Gemini/Gemma mentions) from the 2026-09-18 run, with
the same 3-line source context the live pipeline had, under:

- baseline: HEAD-before-fix dictionary (no aliases) + old rule 4, no rule 9
- treatment: aliased dictionary + new rule 4 exception + rule 9

No ASR re-run: the misheard ASR text is held fixed so only the translation
side differs.

Usage:
    python3 -m real_time_translation.experiments.term_alias_replay \\
        --repeats 3 --name term_alias_replay
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.config import Config
from real_time_translation.translation.llm_translator import LLMTranslator

SOURCE_RUN = Path(
    "experiments/20260918_llm2025_ep8_zenhan_full_lecture_v2_dict.json"
)
DICTIONARY = Path("dictionaries/sessions/LLM2024_8_part1.csv")
BASELINE_DICTIONARY_REV = "HEAD"

# segment index -> (regex a CORRECT translation must match,
#                   regex a WRONG translation matches)
CASES: dict[int, tuple[str, str]] = {
    10: (r"Jiaxian\s*Guo", r"Josh|ジョシュ"),
    12: (r"松尾|Matsuo", r"Muscle|マッスル"),
    44: (r"AIエージェント|AI\s*agent", r"AIME"),
    45: (r"(?i)suspicion[- ]agent", r"(?i)suspension|サスペンション"),
    97: (r"アジェンダ|議題|アジェンダ|目次|概要|内容", r"エージェント"),
    126: (r"松尾|Matsuo", r"Muscle|マッスル"),
    215: (r"Yanqi", r"(?i)yanxi"),
    221: (r"DeepMind|ディープマインド", r"Deepgram"),
    246: (r"(?i)suspicion", r"(?i)suspension|サスペンション|muscle|マッスル"),
    890: (r"Cerebras|セレブラス", r"Celebrace"),
    # controls: correct as heard, must stay unchanged
    56: (r"Gemini.*Gemma|Gemma.*Gemini", r"^$"),
    279: (r"Gemini", r"Gemma"),
    566: (r"Gemma 3n", r"Gemini"),
    75: (r"多言語", r"^$"),
}


def _old_system_prompt() -> str:
    template = LLMTranslator.SYSTEM_PROMPT_TEMPLATE
    template = re.sub(r" EXCEPTION -- ASR ERRORS:[^\n]*", "", template)
    template = re.sub(r"9\. NO PLACEHOLDERS:[^\n]*\n\n", "", template)
    return template


def _baseline_dictionary(tmp: Path) -> Path:
    content = subprocess.run(
        ["git", "show", f"{BASELINE_DICTIONARY_REV}:{DICTIONARY}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    path = tmp / "baseline_dictionary.csv"
    path.write_text(content, encoding="utf-8")
    return path


def _make_translator(config: Config, dictionary: Path, *, old_prompt: bool):
    translator = LLMTranslator(
        provider=config.llm_provider,  # type: ignore[arg-type]
        api_key=(
            config.google_api_key
            if config.llm_provider == "gemini"
            else config.openai_api_key
        )
        or "",
        model=config.gemini_model
        if config.llm_provider == "gemini"
        else config.openai_model,
        dictionary_path=dictionary,
        context_window_size=config.context_window_size,
        thinking_budget=config.gemini_thinking_budget,
        dictionary_dynamic_threshold=config.dictionary_dynamic_threshold,
        dictionary_dynamic_limit=config.dictionary_dynamic_limit,
    )
    if old_prompt:
        translator.SYSTEM_PROMPT_TEMPLATE = _old_system_prompt()
    return translator


async def _translate(translator: LLMTranslator, text: str, context: list[str]):
    out = ""
    async for chunk in translator.translate_stream(
        text, context_lines=context, update_context=False
    ):
        out += chunk
    return out.strip()


async def run(repeats: int) -> dict:
    config = Config.from_env(require_zoom=False)
    segments = json.loads(SOURCE_RUN.read_text(encoding="utf-8"))["results"][
        "segments"
    ]
    with tempfile.TemporaryDirectory() as tmp:
        conditions = {
            "baseline": _make_translator(
                config, _baseline_dictionary(Path(tmp)), old_prompt=True
            ),
            "treatment": _make_translator(config, DICTIONARY, old_prompt=False),
        }
        rows = []
        for idx, (good, bad) in CASES.items():
            seg = segments[idx]
            text = seg["asr"]
            if seg["confidence"] < 0.7:
                text = f"[uncertain: {text}]"
            context = [s["asr"] for s in segments[max(0, idx - 3) : idx]]
            for name, translator in conditions.items():
                for rep in range(repeats):
                    translation = await _translate(translator, text, context)
                    rows.append(
                        {
                            "segment": idx,
                            "condition": name,
                            "repeat": rep,
                            "control": bad == r"^$" or idx in (279, 566),
                            "asr": seg["asr"],
                            "translation": translation,
                            "correct": bool(re.search(good, translation)),
                            "wrong": bool(re.search(bad, translation))
                            if bad != r"^$"
                            else False,
                        }
                    )
                    print(name, idx, rep, translation)
    summary = {}
    for name in conditions:
        for kind in ("target", "control"):
            subset = [
                r
                for r in rows
                if r["condition"] == name and r["control"] == (kind == "control")
            ]
            summary[f"{name}_{kind}"] = {
                "n": len(subset),
                "correct_rate": round(
                    sum(r["correct"] for r in subset) / len(subset), 3
                ),
                "wrong_rate": round(sum(r["wrong"] for r in subset) / len(subset), 3),
            }
    return {
        "models": {"llm_provider": config.llm_provider, "llm_model": config.gemini_model},
        "summary": summary,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--name", default="term_alias_replay")
    args = parser.parse_args(argv)

    started = time.time()
    result = asyncio.run(run(args.repeats))
    date = datetime.now(UTC).strftime("%Y%m%d")
    out_path = Path("experiments") / f"{date}_{args.name}.json"
    payload = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": args.name,
        "input": {"source_run": str(SOURCE_RUN), "cases": sorted(CASES)},
        "repeats": args.repeats,
        **result,
        "elapsed_seconds": round(time.time() - started, 1),
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["summary"], indent=2))
    print(f"wrote {out_path}")

    s = result["summary"]
    notes = (
        f"A/B translation-only replay of {len(CASES)} segments x {args.repeats} "
        f"repeats from {SOURCE_RUN.name} (ASR held fixed). target terms: "
        f"baseline correct={s['baseline_target']['correct_rate']} "
        f"wrong={s['baseline_target']['wrong_rate']} -> treatment correct="
        f"{s['treatment_target']['correct_rate']} wrong="
        f"{s['treatment_target']['wrong_rate']}; controls correct "
        f"{s['baseline_control']['correct_rate']} -> "
        f"{s['treatment_control']['correct_rate']}. treatment = dictionary "
        "aliases + fuzzy retrieval + prompt rule-4 ASR-error exception + rule 9"
    )
    with Path("experiments/results.csv").open("a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(
            [
                payload["date"],
                args.name,
                "llm_architecture",
                str(SOURCE_RUN),
                "",
                "",
                "",
                result["models"]["llm_provider"],
                result["models"]["llm_model"],
                len(result["rows"]),
                "",
                "",
                "",
                "",
                "",
                "",
                notes,
                str(out_path),
            ]
        )


if __name__ == "__main__":
    main()
