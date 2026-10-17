"""Test ASR and translation accuracy with a full file (non-streaming)."""

import asyncio
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from deepgram import DeepgramClient
from dotenv import load_dotenv

from real_time_translation.translation.llm_translator import LLMTranslator

logger = logging.getLogger(__name__)


@dataclass
class CombinedResult:
    """Combined translation result from multiple chunks."""
    latest_slide: str
    kept_terms: list


async def test_accuracy(audio_path: str) -> None:
    """Test ASR and translation accuracy with a full audio file."""
    load_dotenv()

    # Get API keys from environment
    deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
    if not deepgram_api_key:
        print("Error: DEEPGRAM_API_KEY not set in .env")
        return

    llm_provider = os.getenv("LLM_PROVIDER", "gemini")
    if llm_provider == "gemini":
        api_key = os.getenv("GOOGLE_API_KEY")
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    else:
        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        print(f"Error: API key for {llm_provider} not set in .env")
        return

    print(f"Audio file: {audio_path}")
    print(f"LLM Provider: {llm_provider}")
    print(f"Model: {model}")
    print("=" * 60)

    # Step 1: Transcribe with Deepgram Pre-recorded API
    print("\n[1/2] Transcribing with Deepgram...")

    deepgram = DeepgramClient(api_key=deepgram_api_key)

    # Read audio file
    file_path = Path(audio_path)
    with open(audio_path, "rb") as audio_file:
        audio_data = audio_file.read()

    print(f"  File size: {len(audio_data):,} bytes")

    # Detect encoding based on extension
    ext = file_path.suffix.lower()
    encoding_map = {
        ".flac": "flac",
        ".wav": "linear16",
        ".mp3": None,  # Auto-detect
        ".m4a": None,
        ".ogg": "opus",
    }
    encoding = encoding_map.get(ext)
    print(f"  Encoding: {encoding or 'auto-detect'}")

    # Use the new SDK API - transcribe_file with keyword arguments
    # paragraphs=True gives better structure for longer audio
    # keyterm helps with domain-specific vocabulary
    keyterms = [
        "reinforcement learning",
        "offline reinforcement learning",
        "Markov decision process",
        "MDP",
        "five element tuple",
        "tuple",
        "world model",
        "reward model",
        "reward function",
        "transition function",
        "behavior policy",
        "supervised learning",
        "RL",
        "gamma",
        "discounted factor",
        "trajectory dataset",
    ]
    response = deepgram.listen.v1.media.transcribe_file(
        request=audio_data,
        model="nova-3-general",
        language="en",
        smart_format=True,
        punctuate=True,
        paragraphs=True,
        encoding=encoding,
        keyterm=keyterms,
    )

    # Debug info
    metadata = response.metadata
    print(f"  Duration: {metadata.duration:.2f} seconds")
    print(f"  Channels: {metadata.channels}")

    # Extract transcript
    channel = response.results.channels[0]
    alternative = channel.alternatives[0]
    transcript = alternative.transcript
    confidence = alternative.confidence

    print(f"  Confidence: {confidence:.2%}")
    print(f"  Words detected: {len(alternative.words)}")

    print("\n" + "=" * 60)
    print("【ASR結果 (Deepgram)】")
    print("=" * 60)
    print(transcript)

    # Step 2: Translate with LLM
    print("\n" + "=" * 60)
    print("[2/2] Translating with LLM...")
    print("=" * 60)

    logger.debug("Transcript length: %d chars, %d words", len(transcript), len(transcript.split()))

    translator = LLMTranslator(
        provider=llm_provider,
        api_key=api_key,
        model=model,
        source_language="English",
        target_language="Japanese",
    )

    await translator.prepare()

    # Split long text into sentences and translate in chunks
    # This prevents truncation issues with long texts
    sentences = re.split(r'(?<=[.!?])\s+', transcript)
    logger.debug("Split into %d sentences", len(sentences))

    all_translations = []
    all_kept_terms = []
    chunk_size = 3  # Translate 3 sentences at a time

    for i in range(0, len(sentences), chunk_size):
        chunk = " ".join(sentences[i:i + chunk_size])
        chunk_num = i // chunk_size + 1
        total_chunks = (len(sentences) + chunk_size - 1) // chunk_size
        logger.debug("Translating chunk %d/%d (%d chars)", chunk_num, total_chunks, len(chunk))

        try:
            chunk_result = await asyncio.wait_for(translator.translate(chunk), timeout=60.0)
            all_translations.append(chunk_result.latest_slide)
            all_kept_terms.extend(chunk_result.kept_terms)
        except TimeoutError:
            logger.error("Chunk %d timed out!", chunk_num)
            all_translations.append(f"[翻訳タイムアウト: {chunk[:50]}...]")

    # Combine results
    result = CombinedResult(
        latest_slide="\n".join(all_translations),
        kept_terms=list(set(all_kept_terms))
    )

    print("\n" + "=" * 60)
    print("【翻訳結果】")
    print("=" * 60)
    print(result.latest_slide)

    if result.kept_terms:
        print(f"\n保持用語: {', '.join(result.kept_terms)}")

    print("\n" + "=" * 60)
    print("完了!")
    print("=" * 60)

    # Save results to file
    output_file = Path(audio_path).stem + "_results.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"Audio file: {audio_path}\n")
        f.write(f"Duration: {metadata.duration:.2f} seconds\n")
        f.write(f"Confidence: {confidence:.2%}\n")
        f.write(f"Words detected: {len(alternative.words)}\n")
        f.write("\n" + "=" * 60 + "\n")
        f.write("【ASR結果 (Deepgram)】\n")
        f.write("=" * 60 + "\n")
        f.write(transcript + "\n")
        f.write("\n" + "=" * 60 + "\n")
        f.write("【翻訳結果】\n")
        f.write("=" * 60 + "\n")
        f.write(result.latest_slide + "\n")
        if result.kept_terms:
            f.write(f"\n保持用語: {', '.join(result.kept_terms)}\n")
    print(f"\n結果を保存しました: {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/manual_asr_accuracy_check.py <audio_file>")
        print("\nSupported formats: WAV, FLAC, MP3, etc.")
        print("\nExample:")
        print("  uv run python scripts/manual_asr_accuracy_check.py clip_386-464.flac")
        sys.exit(1)

    asyncio.run(test_accuracy(sys.argv[1]))
