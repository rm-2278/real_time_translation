"""Produce a careful ground-truth transcription of a video clip via Gemini.

Unlike the real-time pipeline's streaming ASR (optimized for low latency,
one pass, no lookahead), this uploads the clip's video+audio to a strong
Gemini model and asks for a careful, non-real-time transcription -- with
low-confidence spots explicitly flagged for manual review. Seeing the
slides alongside the audio lets the model resolve ambiguous terms the way
a human transcriber would (e.g. a garbled "KV catch" next to a slide
labeled "KV Cache").

Usage:
  uv run python scripts/ground_truth_transcribe.py \
      experiments/clips/hardest_5min_64m07-69m07.mp4 \
      --out experiments/clips/hardest_5min_ground_truth.txt
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

TRANSCRIPTION_PROMPT = """You are producing a careful ground-truth \
transcription of the English audio in this video clip, for use as a \
reference in evaluating an automatic speech recognition system. This clip \
is known to be difficult: it contains non-native/accented English, dense \
technical ML jargon, and disfluent conversational Q&A speech.

Instructions:
1. Transcribe EXACTLY what is said, verbatim, including disfluencies (um, \
uh, repeated words, false starts). Do not clean up grammar or smooth over \
repetitions -- this must reflect exactly what was spoken, not a polished \
version.
2. Break the transcript into short lines, each prefixed with an \
approximate timestamp in [mm:ss] format relative to the START of this \
clip (not the original video).
3. You may use the visible slide content to help you correctly identify \
ambiguous technical terms when the speaker's pronunciation is unclear \
(e.g. a slide showing "KV Cache" resolves a mumbled pronunciation) -- but \
only for terms actually being discussed, not to guess unrelated content.
4. For any word, phrase, or short passage where you are NOT confident in \
the transcription -- due to unclear audio, heavy accent, overlapping \
speech, or genuine ambiguity -- mark it inline as [uncertain: your best \
guess] and ALSO list it in the UNCERTAIN SPOTS section at the end with \
its timestamp and a one-line reason.
5. If a passage is truly inaudible, write [inaudible] rather than \
guessing.

Output in exactly this format:

<transcript>
[mm:ss] text...
[mm:ss] text...
</transcript>

<uncertain_spots>
- [mm:ss] "transcribed text" -- reason for uncertainty
- [mm:ss] "transcribed text" -- reason for uncertainty
</uncertain_spots>
"""


async def transcribe(video_path: Path, *, api_key: str, model: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    print(f"Uploading {video_path}...")
    uploaded = await client.aio.files.upload(file=video_path)

    # Video files need server-side processing before they're usable.
    while uploaded.state == "PROCESSING":
        print("  processing...")
        await asyncio.sleep(3)
        uploaded = await client.aio.files.get(name=uploaded.name)
    if uploaded.state != "ACTIVE":
        raise RuntimeError(f"File upload failed, state={uploaded.state}")

    print("Transcribing (this may take a minute)...")
    t0 = time.time()
    response = await client.aio.models.generate_content(
        model=model,
        contents=[TRANSCRIPTION_PROMPT, uploaded],
        config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=32768),
    )
    print(f"Done in {time.time() - t0:.1f}s")

    await client.aio.files.delete(name=uploaded.name)
    return response.text or ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.1-pro-preview")
    return parser


def main(argv: list[str] | None = None) -> None:
    from real_time_translation.config import Config

    args = build_parser().parse_args(argv)
    config = Config.from_env(require_zoom=False)
    if not config.google_api_key:
        raise RuntimeError("GOOGLE_API_KEY is required")

    text = asyncio.run(
        transcribe(args.video, api_key=config.google_api_key, model=args.model)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"Wrote: {args.out}")


if __name__ == "__main__":
    main()
