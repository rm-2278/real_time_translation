"""LLM-based translator with Gemini/OpenAI support and context caching.

Uses direct provider SDK calls (google-genai / openai) with real token
streaming instead of LangChain's `with_structured_output`, which blocks
until the full JSON response is generated. That blocking + structured-output
overhead was measured to add seconds of latency with negligible quality
benefit, and made real-time (word-by-word) caption rendering impossible.
As a deliberate trade-off, this path no longer extracts `kept_terms` via
a second structured field (it's always empty) — the same trade already
validated in services/translator/fast_translator.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from real_time_translation.translation.dictionary import DictionaryEntry, TermDictionary
from real_time_translation.translation.domain_packs import (
    DEFAULT_DOMAIN_PACKS_DIR,
    resolve_domain_pack,
)


@dataclass(frozen=True)
class TranslationOutput:
    """Application-level translation output."""

    latest_slide: str
    kept_terms: list[str]
    slide_window: list[str]


class LLMTranslator:
    """Translator using Gemini or OpenAI with contextual prompting."""

    SYSTEM_PROMPT_TEMPLATE = """You are a professional simultaneous interpreter translating from {source_language} to {target_language}.

You will be provided with <context> (previous utterances) and a <target> (the current text to translate).

Strict Rules:
1. TRANSLATE TARGET ONLY: Translate ONLY the text inside <target>. Use <context> purely to resolve pronouns, understand the ongoing grammatical structure, and correct phonetic ASR errors (e.g., 'laundry model' -> 'language model', 'three d deficient' -> '3D diffusion').
2. NO FORCED CLOSURES: The <target> is often a mid-sentence fragment from a live stream. DO NOT add terminal punctuation (e.g., periods, '。', 'です', 'ます') unless the <target> text clearly concludes a complete grammatical thought. 
3. USE CONTINUATIONS: If the <target> is a fragment, translate it as a continuation (e.g., using noun phrases, 'て' forms, or dangling particles) so it flows naturally into whatever text might come next.
4. PRESERVE TERMINOLOGY: Keep proper nouns, acronyms, and code identifiers EXACTLY as they appear. Do not translate, transliterate, or normalize them. If a term is ambiguous or unknown, keep it unchanged.
5. IGNORE PADDING: Ignore any content inside <cache_padding>...</cache_padding>.
6. TERMINOLOGY DICTIONARY: A <dictionary> block may appear below, or inline with the current message alongside <context>/<target>. Either way, use its exact target-language translations for any matching terms.
7. LOW-CONFIDENCE MARKER: If <target> is wrapped exactly as `[uncertain: ...]`, that wrapper is a confidence hint for you, not literal content to translate. Translate only the text inside it (favor a cautious, literal reading over a confident guess) and NEVER reproduce the `[uncertain: ...]`/`[不確か: ...]` wrapper itself in your output.
8. SMOOTH DISFLUENCY, DON'T TRANSCRIBE IT: <target> is live, unedited speech, not prose -- it will contain filler words ('um', 'uh', 'like', 'you know'), false starts, and immediate stutter-repeats (e.g. 'I I think', 'made made', 'and also and also'). Act like a human simultaneous interpreter, not a transcriptionist: drop filler words and collapse an immediate exact stutter-repeat into a single instance, producing a clean, natural target-language sentence. NEVER drop or alter substantive content, technical terms, numbers, or the speaker's actual meaning -- when a repetition might be deliberate emphasis rather than disfluency, keep it rather than guess wrong.

Maintain the original tone and style.
{dictionary_section}"""

    def __init__(
        self,
        provider: Literal["gemini", "openai"],
        api_key: str,
        model: str,
        source_language: str = "English",
        target_language: str = "Japanese",
        dictionary_path: Path | str | None = None,
        context_window_size: int = 3,
        thinking_budget: int | None = 0,
        dictionary_dynamic_threshold: int = 80,
        dictionary_dynamic_limit: int = 30,
        domain_packs: list[str] | None = None,
        domain_packs_dir: Path | str = DEFAULT_DOMAIN_PACKS_DIR,
        openai_temperature: float | None = 0.3,
    ) -> None:
        """Initialize LLM translator.

        Args:
            provider: LLM provider ("gemini" or "openai")
            api_key: API key for the provider
            model: Model name to use
            source_language: Source language name
            target_language: Target language name
            dictionary_path: Optional path to CSV dictionary file. Loaded
                after `domain_packs`, so its entries take precedence over
                any pack entry for the same term.
            context_window_size: Number of previous lines to keep as context
            thinking_budget: Gemini "thinking" token budget. 0 disables extended
                reasoning for lower latency (measured ~700-1000ms TTFT on
                gemini-2.5-flash vs multi-second TTFT with thinking enabled on
                gemini-3.x models). None omits the parameter entirely, for
                models that reject it.
            dictionary_dynamic_threshold: Above this many dictionary entries,
                stop dumping the full dictionary into the (cached) system
                prompt and instead inject only per-request relevant terms
                into the (uncached) user prompt -- see `_is_dynamic_mode`.
                Below it, the full dictionary is cached once and reused,
                which also gives the LLM a shot at correcting ASR errors on
                terms that aren't a literal substring match.
            dictionary_dynamic_limit: Max terms injected per request once in
                dynamic mode.
            domain_packs: Names of curated glossary packs to load first
                (e.g. ["machine_learning", "particle_physics"]), before
                `dictionary_path`. See `translation.domain_packs`.
            domain_packs_dir: Directory containing `<name>.csv` pack files.
            openai_temperature: Sampling temperature for the OpenAI provider.
                None omits the parameter (API default), for models that
                reject a non-default value -- confirmed `gpt-5.6-luna`
                400s on anything but the default (1); `gpt-5.4-mini`/
                `gpt-5.4-nano` accept 0.3 fine. Ignored for `provider="gemini"`.
        """
        self._provider = provider
        self._api_key = api_key
        self._model_name = model
        self._source_language = source_language
        self._target_language = target_language
        self._context_window_size = context_window_size
        self._thinking_budget = thinking_budget
        self._dictionary_dynamic_threshold = dictionary_dynamic_threshold
        self._dictionary_dynamic_limit = dictionary_dynamic_limit
        self._domain_packs_dir = domain_packs_dir
        self._openai_temperature = openai_temperature
        self._openai_temperature_unsupported = False

        self._context_buffer: list[str] = []
        self._slide_window: list[str] = []
        self._system_prompt_cache: str | None = None

        self._gemini_client: Any | None = None
        self._gemini_cache_name: str | None = None
        self._gemini_cache_ttl = timedelta(hours=12)

        self._openai_client: Any | None = None

        # Load domain packs first, then the session dictionary on top, so
        # session-specific entries override same-named pack entries.
        self._dictionary = TermDictionary()
        for pack_name in domain_packs or []:
            self.load_domain_pack(pack_name)
        if dictionary_path:
            self.load_dictionary(dictionary_path)

    def load_domain_pack(self, name: str) -> int:
        """Load a curated domain glossary pack by name.

        Args:
            name: Pack name (CSV stem under `domain_packs_dir`), e.g.
                "machine_learning" or "particle_physics"

        Returns:
            Number of entries loaded

        Raises:
            FileNotFoundError: If no pack named `name` exists
        """
        path = resolve_domain_pack(name, self._domain_packs_dir)
        return self.load_dictionary(path)

    def load_dictionary(self, path: Path | str) -> int:
        """Load terminology dictionary from CSV file.

        Args:
            path: Path to CSV file (format: source_term,target_term,notes)

        Returns:
            Number of entries loaded
        """
        count = self._dictionary.load_csv(path)
        self._system_prompt_cache = None
        self._invalidate_gemini_cache()
        return count

    @property
    def dictionary(self) -> TermDictionary:
        """Get the terminology dictionary."""
        return self._dictionary

    @property
    def source_language(self) -> str:
        """Source language name."""
        return self._source_language

    @property
    def target_language(self) -> str:
        """Target language name."""
        return self._target_language

    def merge_supplementary_entries(self, entries: list[DictionaryEntry]) -> int:
        """Merge a best-effort/supplementary source into the dictionary.

        Unlike `load_dictionary`/`load_domain_pack`, entries here never
        override an already-loaded term (see `TermDictionary.add_if_absent`)
        -- for auto-extracted pre-brief terms (see `preload.auto_preload`),
        which are less deliberately curated than a domain pack or session
        `dictionary_path`.

        Returns:
            Number of entries actually added
        """
        added = self._dictionary.add_if_absent(entries)
        if added:
            self._system_prompt_cache = None
            if not self._is_dynamic_mode():
                self._invalidate_gemini_cache()
        return added

    def _is_dynamic_mode(self) -> bool:
        """Whether the dictionary is too large to dump into every prompt."""
        return len(self._dictionary) > self._dictionary_dynamic_threshold

    async def prepare(self) -> None:
        """Warm up translator state (e.g., create Gemini cache)."""
        if self._provider == "gemini":
            await asyncio.to_thread(self._ensure_gemini_cache)

    def refresh_cache(self) -> None:
        """Invalidate cached system prompt and Gemini cache."""
        self._system_prompt_cache = None
        self._invalidate_gemini_cache()

    def _invalidate_gemini_cache(self) -> None:
        if self._gemini_cache_name and self._gemini_client:
            with contextlib.suppress(Exception):
                self._gemini_client.caches.delete(name=self._gemini_cache_name)

        self._gemini_cache_name = None

    def _get_openai_client(self) -> Any:
        """Get or create the direct OpenAI async client."""
        if self._openai_client is None:
            from openai import AsyncOpenAI

            self._openai_client = AsyncOpenAI(api_key=self._api_key)
        return self._openai_client

    def _get_system_prompt(self) -> str:
        """Get system prompt with language settings and dictionary.

        Returns:
            Formatted system prompt
        """
        if self._system_prompt_cache is not None:
            return self._system_prompt_cache

        # In dynamic mode the dictionary is injected per-request into the
        # (uncached) user prompt instead -- see `_build_user_prompt` -- so
        # the cached system prompt stays small and stable regardless of how
        # large the dictionary grows.
        dictionary_section = ""
        if self._dictionary and not self._is_dynamic_mode():
            formatted = self._dictionary.format_for_prompt()
            dictionary_section = f"\n\n<dictionary>\n{formatted}\n</dictionary>"

        self._system_prompt_cache = self.SYSTEM_PROMPT_TEMPLATE.format(
            source_language=self._source_language,
            target_language=self._target_language,
            dictionary_section=dictionary_section,
        )
        return self._system_prompt_cache

    def _build_user_prompt(
        self,
        text: str,
        *,
        context_lines: list[str] | None = None,
        prior_translation: str | None = None,
    ) -> str:
        if context_lines is None:
            context_lines = self._context_buffer[-self._context_window_size :]
        else:
            context_lines = context_lines[-self._context_window_size :]
        context_block = "\n".join(context_lines)

        dictionary_block = ""
        if self._dictionary and self._is_dynamic_mode():
            relevant = self._dictionary.relevant_entries(
                text,
                limit=self._dictionary_dynamic_limit,
                extra_context=context_lines,
            )
            if relevant:
                formatted = self._dictionary.format_for_prompt(relevant)
                dictionary_block = f"<dictionary>\n{formatted}\n</dictionary>\n"

        # h-continuation-context-anchor (research_agent/state/hypotheses.json):
        # for a continuation batch of an in-progress utterance, anchor the
        # retranslation to what was already shown on screen for this same
        # utterance, instead of leaving the model to reconstruct it from
        # <target> alone with no memory of its own prior wording.
        prior_translation_block = ""
        if prior_translation:
            prior_translation_block = (
                "<prior_translation>\n"
                f"{prior_translation}\n"
                "</prior_translation>\n"
                "The above is your own translation of this same utterance so "
                "far, already shown to the viewer. <target> is the complete "
                "utterance heard so far, including that earlier part. Preserve "
                "and extend <prior_translation> rather than rewording it, "
                "unless the new text in <target> changes its meaning.\n"
            )

        return (
            f"{dictionary_block}"
            f"{prior_translation_block}"
            f"<context>\n{context_block}\n</context>\n<target>\n{text}\n</target>"
        )

    def _get_gemini_client(self) -> Any:
        if self._gemini_client is None:
            from google import genai

            self._gemini_client = genai.Client(api_key=self._api_key)
        return self._gemini_client

    def _create_gemini_cache(self) -> str:
        from google.genai import types

        client = self._get_gemini_client()
        base_system_prompt = self._get_system_prompt()
        ttl_seconds = int(self._gemini_cache_ttl.total_seconds())

        def _create(system_instruction: str) -> str:
            config = types.CreateCachedContentConfig(
                display_name="real-time-translation-system",
                system_instruction=system_instruction,
                contents=None,
                ttl=f"{ttl_seconds}s",
            )
            cache = client.caches.create(model=self._model_name, config=config)
            return cache.name

        try:
            return _create(base_system_prompt)
        except Exception as exc:
            message = str(exc)
            match = re.search(
                r"total_token_count=(\d+), min_total_token_count=(\d+)",
                message,
            )
            if "Cached content is too small" not in message or match is None:
                raise

            total = int(match.group(1))
            minimum = int(match.group(2))
            extra = max(0, minimum - total) + 256
            padding = (
                "\n\n<cache_padding>\n"
                "IGNORE EVERYTHING IN THIS TAG. It only exists to satisfy the "
                "minimum cached-content token requirement.\n"
                + ("PAD " * extra)
                + "\n</cache_padding>"
            )
            return _create(base_system_prompt + padding)

    def _ensure_gemini_cache(self) -> str | None:
        """Create the Gemini context cache if possible.

        Context caching requires a paid tier (free tier has a cached-content
        storage quota of 0 and returns 429 RESOURCE_EXHAUSTED, not just the
        "too small" error `_create_gemini_cache` already handles). Caching is
        a latency optimization, not a correctness requirement -- translation
        works fine without it (falls back to sending the system prompt on
        every request), so failures here must never be fatal.
        """
        if self._gemini_cache_name is None:
            try:
                self._gemini_cache_name = self._create_gemini_cache()
            except Exception as exc:  # noqa: BLE001
                print(
                    f"Gemini context caching unavailable, continuing without it: {exc}"
                )
                return None
        return self._gemini_cache_name

    def _gemini_generation_config(self, *, cache_name: str | None) -> Any:
        from google.genai import types

        kwargs: dict[str, Any] = dict(
            temperature=0.2,
            max_output_tokens=512,
            top_p=0.95,
            top_k=40,
        )
        if self._thinking_budget is not None:
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=self._thinking_budget
            )
        if cache_name:
            kwargs["cached_content"] = cache_name
        else:
            kwargs["system_instruction"] = self._get_system_prompt()
        return types.GenerateContentConfig(**kwargs)

    async def _translate_gemini_stream(self, prompt: str) -> AsyncIterator[str]:
        """Stream Gemini output token-by-token via the direct async SDK.

        Uses context caching for the (large, static) system prompt so each
        request only pays for the small per-utterance user prompt.
        """
        client = self._get_gemini_client()
        cache_name = self._gemini_cache_name
        config = self._gemini_generation_config(cache_name=cache_name)

        stream = await client.aio.models.generate_content_stream(
            model=self._model_name,
            contents=prompt,
            config=config,
        )
        async for chunk in stream:
            if chunk.text:
                yield chunk.text

    def _openai_kwargs(
        self, prompt: str, *, include_temperature: bool
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "messages": [
                {"role": "system", "content": self._get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "max_completion_tokens": 512,
            "stream": True,
        }
        if include_temperature and self._openai_temperature is not None:
            kwargs["temperature"] = self._openai_temperature
        return kwargs

    async def _translate_openai_stream(self, prompt: str) -> AsyncIterator[str]:
        """Stream OpenAI output token-by-token."""
        client = self._get_openai_client()
        try:
            stream = await client.chat.completions.create(
                **self._openai_kwargs(
                    prompt, include_temperature=not self._openai_temperature_unsupported
                )
            )
        except Exception as exc:
            # Some models (confirmed: gpt-5.6-luna) reject any non-default
            # temperature outright (400). Retry once without it and
            # remember for the rest of this translator's lifetime, rather
            # than erroring every single translation for the whole
            # session -- an unsupported sampling parameter shouldn't be
            # able to break translation entirely.
            if self._openai_temperature_unsupported or "temperature" not in str(exc):
                raise
            self._openai_temperature_unsupported = True
            stream = await client.chat.completions.create(
                **self._openai_kwargs(prompt, include_temperature=False)
            )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def translate_stream(
        self,
        text: str,
        *,
        context_lines: list[str] | None = None,
        update_context: bool = True,
        prior_translation: str | None = None,
    ) -> AsyncIterator[str]:
        """Translate text, yielding output chunks as they are generated.

        Args:
            text: Text to translate
            context_lines: Optional explicit context lines (stateless mode,
                used by parallel workers so concurrent calls don't race on
                the shared internal context buffer)
            update_context: Whether to update internal context buffers
                after the translation completes
            prior_translation: This same utterance's own most-recently-
                emitted translation, for a continuation-batch retranslation
                to anchor to (h-continuation-context-anchor). None for a
                first/only batch of an utterance.

        Yields:
            Translation text chunks (concatenate for the full translation)
        """
        if not text.strip():
            return

        prompt = self._build_user_prompt(
            text, context_lines=context_lines, prior_translation=prior_translation
        )

        full_text = ""
        if self._provider == "gemini":
            async for chunk in self._translate_gemini_stream(prompt):
                full_text += chunk
                yield chunk
        else:
            async for chunk in self._translate_openai_stream(prompt):
                full_text += chunk
                yield chunk

        should_update_context = update_context and context_lines is None
        if should_update_context:
            self._context_buffer.append(text)
            if len(self._context_buffer) > self._context_window_size:
                self._context_buffer.pop(0)
            self._slide_window.append(full_text.strip())
            if len(self._slide_window) > self._context_window_size:
                self._slide_window.pop(0)

    async def translate(
        self,
        text: str,
        *,
        context_lines: list[str] | None = None,
        update_context: bool = True,
    ) -> TranslationOutput:
        """Translate text using LLM (non-streaming convenience wrapper).

        Args:
            text: Text to translate
            context_lines: Optional explicit context lines (stateless mode)
            update_context: Whether to update internal context buffers

        Returns:
            Translation output including the latest slide and current slide window
        """
        if not text.strip():
            return TranslationOutput(latest_slide="", kept_terms=[], slide_window=[])

        full_text = ""
        async for chunk in self.translate_stream(
            text, context_lines=context_lines, update_context=update_context
        ):
            full_text += chunk

        return TranslationOutput(
            latest_slide=full_text.strip(),
            kept_terms=[],
            slide_window=list(self._slide_window),
        )

    @property
    def slide_window(self) -> list[str]:
        """Current slide window (recent translated lines)."""
        return list(self._slide_window)

    def context_snapshot(self) -> list[str]:
        """Snapshot of the current source-context buffer (for stateless calls)."""
        return list(self._context_buffer[-self._context_window_size :])

    def commit_context(self, source_text: str, translated_text: str) -> None:
        """Append a completed translation to the shared context buffers.

        Used by callers that translate statelessly (explicit `context_lines`,
        `update_context=False`) so concurrent workers can commit results
        without racing on `translate_stream`'s internal buffer mutation.
        """
        self._context_buffer.append(source_text)
        if len(self._context_buffer) > self._context_window_size:
            self._context_buffer.pop(0)
        self._slide_window.append(translated_text)
        if len(self._slide_window) > self._context_window_size:
            self._slide_window.pop(0)

    def clear_context(self) -> None:
        """Clear the context buffer."""
        self._context_buffer.clear()
        self._slide_window.clear()
