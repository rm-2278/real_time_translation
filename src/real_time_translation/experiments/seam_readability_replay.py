"""A/B replay: does showing the model its own previous captions fix bad seams?

2026-10-13 review: captions read fine one at a time but badly in sequence --
the same thing said twice across two cards, a clause whose subject or
predicate was never shown, a fragment that dangles. Cause: <context> carries
only the previous *source* lines, so the model cannot see the Japanese the
viewer just read.

ASR segments are held fixed (post dangling-carry, from a recorded run) and
re-translated *sequentially* per variant, each variant feeding on its own
earlier output the way the live pipeline's rolling context does. The result
is judged on windows of consecutive captions as the viewer reads them:
an LLM judge counts seam defects (repeat / missing subject-predicate /
dangling / broken join) and omissions against the source; a cheap
deterministic repeat score cross-checks it.

Usage:
    uv run python -m real_time_translation.experiments.seam_readability_replay \\
        --variants base,prev --start 0 --count 120 --name seam_readability_replay
"""

# ruff: noqa: E501  (judge prompt / notes are long prose)
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.config import Config
from real_time_translation.translation.llm_translator import LLMTranslator
from real_time_translation.translation.prompts import SEAMLESS_CAPTIONS_INSTRUCTIONS

SOURCE_RUN = Path(
    "experiments/20260924_llm2024_8_part1_review_fixes_v2_7m30_21m50.json"
)
DICTIONARY = Path("dictionaries/sessions/LLM2024_8_part1.csv")
WINDOW = 6
JUDGE_MODEL = "gemini-3-flash-preview"

RULES_V1 = SEAMLESS_CAPTIONS_INSTRUCTIONS.split("\n   d. ")[0]
RULES_V2 = SEAMLESS_CAPTIONS_INSTRUCTIONS

# name -> (extra system instructions, number of previous captions shown)
VARIANTS: dict[str, tuple[str | None, int]] = {
    "base": (None, 0),
    "prev": (None, 3),
    "prev_rules": (RULES_V1, 3),
    "prev_rules2": (RULES_V2, 3),
    "prev5_rules2": (RULES_V2, 5),
}

_JUDGE_PROMPT = """You review live Japanese subtitles translated from English speech.
The viewer reads the numbered captions below IN ORDER, one after another, each replacing the last. Judge how readable the SEQUENCE is, not each line alone.

Count only real defects of these kinds:
- REPEAT: the same content/phrase is stated again in a neighbouring caption (a viewer would notice the redundancy).
- SUBJ_PRED: a sentence whose subject or predicate cannot be recovered from the captions (who does what is unclear, or a predicate never arrives / has no matching subject).
- DANGLING: a caption ends or starts leaving a grammatical gap that the neighbouring caption does not resolve.
- BAD_JOIN: reading two adjacent captions together is ungrammatical or nonsensical.
Also count ADDITION: content in the captions that is NOT in the English source or contradicts/changes its meaning (invented words, wrong claims; ignore harmless politeness).
Also count OMISSION: substantive content in the English source with no counterpart in the captions (ignore fillers and stutters).

English source (aligned line by line):
{source}

Japanese captions in display order:
{captions}

Reply with ONLY JSON:
{{"REPEAT": n, "SUBJ_PRED": n, "DANGLING": n, "BAD_JOIN": n, "OMISSION": n, "ADDITION": n, "examples": ["short description of the worst 1-3 defects, citing caption numbers"]}}"""

_judge_client = None
# Network drops (httpx.ReadError) have killed multi-minute runs; every
# translation/judge result is cached on disk so a rerun resumes where it died.
CACHE_PATH = Path(os.environ.get("SEAM_CACHE", ".seam_replay_cache.json"))
_cache: dict[str, object] = {}


def _cache_load() -> None:
    if CACHE_PATH.exists():
        _cache.update(json.loads(CACHE_PATH.read_text(encoding="utf-8")))


def _cached(key_parts: object, fn):
    key = hashlib.sha256(json.dumps(key_parts, ensure_ascii=False).encode()).hexdigest()
    if key not in _cache:
        _cache[key] = fn()
        CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    return _cache[key]


def _judge(source_lines: list[str], caption_lines: list[str]) -> dict:
    return _cached(
        ["judge", JUDGE_MODEL, _JUDGE_PROMPT, source_lines, caption_lines],
        lambda: _judge_uncached(source_lines, caption_lines),
    )


def _judge_uncached(source_lines: list[str], caption_lines: list[str]) -> dict:
    global _judge_client
    from dotenv import load_dotenv
    from google import genai
    from google.genai import types

    if _judge_client is None:
        load_dotenv(override=True)
        _judge_client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    prompt = _JUDGE_PROMPT.format(
        source="\n".join(f"{i + 1}. {t}" for i, t in enumerate(source_lines)),
        captions="\n".join(f"{i + 1}. {t}" for i, t in enumerate(caption_lines)),
    )
    for attempt in range(6):
        try:
            resp = _judge_client.models.generate_content(
                model=JUDGE_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0),
            )
            match = re.search(r"\{.*\}", resp.text, re.DOTALL)
            return json.loads(match.group(0))
        except Exception as exc:  # noqa: BLE001 -- retry transient failures
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"judge failed repeatedly: {last!r}")


def repeat_score(captions: list[str], n: int = 5) -> int:
    """Deterministic cross-check: character n-grams shared by adjacent captions."""
    total = 0
    for a, b in zip(captions, captions[1:], strict=False):
        grams = {a[i : i + n] for i in range(len(a) - n + 1)}
        total += sum(1 for i in range(len(b) - n + 1) if b[i : i + n] in grams)
    return total


def _make_translator(config: Config, extra: str | None) -> LLMTranslator:
    return LLMTranslator(
        provider=config.llm_provider,  # type: ignore[arg-type]
        api_key=config.google_api_key or "",
        model=config.gemini_model,
        dictionary_path=DICTIONARY,
        context_window_size=config.context_window_size,
        thinking_budget=config.gemini_thinking_budget,
        dictionary_dynamic_threshold=config.dictionary_dynamic_threshold,
        dictionary_dynamic_limit=config.dictionary_dynamic_limit,
        extra_system_instructions=extra,
    )


async def _translate_cached(translator, key, text, context, prev, target_chars) -> str:
    h = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()
    if h in _cache:
        return str(_cache[h])
    for attempt in range(6):
        try:
            out = ""
            async for chunk in translator.translate_stream(
                text,
                context_lines=context,
                update_context=False,
                previous_translations=prev,
                target_chars=target_chars,
            ):
                out += chunk
            _cache[h] = out.strip()
            CACHE_PATH.write_text(
                json.dumps(_cache, ensure_ascii=False), encoding="utf-8"
            )
            return out.strip()
        except Exception:  # noqa: BLE001 -- transient network/API failures
            await asyncio.sleep(3 * (attempt + 1))
    raise RuntimeError("translation failed repeatedly")


async def _run_variant(config: Config, name: str, segments: list[dict]) -> list[str]:
    extra, n_prev = VARIANTS[name]
    translator = _make_translator(config, extra)
    outputs: list[str] = []
    for i, seg in enumerate(segments):
        text = seg["asr"]
        if seg["confidence"] < 0.7:
            text = f"[uncertain: {text}]"
        context = [s["asr"] for s in segments[max(0, i - 3) : i]]
        prev = outputs[-n_prev:] if n_prev else None
        # The recorded run used reading_speed_budget_translation at
        # 4.5 chars/s; without it, translations run ~37% longer and the
        # captions cannot be read in time (video lag exploded to 41s).
        target_chars = max(
            8, round(max(seg["asr_end_time"] - seg["asr_start_time"], 0.5) * 4.5)
        )
        key = [
            "tr",
            name,
            extra,
            text,
            context,
            prev,
            target_chars,
            config.gemini_model,
        ]
        outputs.append(
            await _translate_cached(translator, key, text, context, prev, target_chars)
        )
    return outputs


def _score(segments: list[dict], captions: list[str]) -> dict:
    totals = {
        k: 0
        for k in ("REPEAT", "SUBJ_PRED", "DANGLING", "BAD_JOIN", "OMISSION", "ADDITION")
    }
    windows = []
    for lo in range(0, len(segments) - WINDOW + 1, WINDOW):
        src = [s["asr"] for s in segments[lo : lo + WINDOW]]
        cap = captions[lo : lo + WINDOW]
        verdict = _judge(src, cap)
        for k in totals:
            totals[k] += int(verdict.get(k, 0) or 0)
        windows.append({"start": lo, "verdict": verdict})
    n = len(windows)
    seam = sum(totals[k] for k in ("REPEAT", "SUBJ_PRED", "DANGLING", "BAD_JOIN"))
    return {
        "windows": n,
        "totals": totals,
        "seam_defects_per_window": round(seam / n, 3) if n else 0.0,
        "omissions_per_window": round(totals["OMISSION"] / n, 3) if n else 0.0,
        "additions_per_window": round(totals["ADDITION"] / n, 3) if n else 0.0,
        "total_chars": sum(map(len, captions)),
        "repeat_ngram_score": repeat_score(captions),
        "mean_chars_per_caption": round(sum(map(len, captions)) / len(captions), 1),
        "window_detail": windows,
    }


async def run(variants: list[str], start: int, count: int) -> dict:
    _cache_load()
    config = Config.from_env(require_zoom=False)
    segments = json.loads(SOURCE_RUN.read_text(encoding="utf-8"))["results"][
        "segments"
    ][start : start + count]
    outs = await asyncio.gather(*(_run_variant(config, v, segments) for v in variants))
    result = {}
    for name, captions in zip(variants, outs, strict=True):
        result[name] = {"captions": captions, **_score(segments, captions)}
        r = result[name]
        print(
            name,
            "seam/win",
            r["seam_defects_per_window"],
            "omit/win",
            r["omissions_per_window"],
            "add/win",
            r["additions_per_window"],
            "chars",
            r["total_chars"],
            "rep_ngram",
            r["repeat_ngram_score"],
            r["totals"],
        )
    return {
        "models": {"llm": config.gemini_model, "judge": JUDGE_MODEL},
        "segments": [s["asr"] for s in segments],
        "variants": result,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", default="base,prev,prev_rules")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--name", default="seam_readability_replay")
    parser.add_argument("--no-log", action="store_true", help="skip results.csv")
    args = parser.parse_args(argv)

    started = time.time()
    variants = args.variants.split(",")
    result = asyncio.run(run(variants, args.start, args.count))
    date = datetime.now(UTC).strftime("%Y%m%d")
    out_path = Path("experiments") / f"{date}_{args.name}.json"
    payload = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": args.name,
        "input": {
            "source_run": str(SOURCE_RUN),
            "start": args.start,
            "count": args.count,
        },
        **result,
        "elapsed_seconds": round(time.time() - started, 1),
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {out_path}")
    if args.no_log:
        return
    notes = (
        "seam readability A/B (ASR fixed, sequential replay, LLM-judge windows of 6): "
        + "; ".join(
            f"{v}: seam/win={r['seam_defects_per_window']} omit/win={r['omissions_per_window']} rep5={r['repeat_ngram_score']}"
            for v, r in result["variants"].items()
        )
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
                "gemini",
                result["models"]["llm"],
                args.count * len(variants),
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
