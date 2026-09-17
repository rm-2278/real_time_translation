"""Configuration management for real-time translation."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from real_time_translation.translation.domain_packs import DEFAULT_DOMAIN_PACKS_DIR


@dataclass
class Config:
    """Application configuration."""

    # Deepgram
    deepgram_api_key: str

    # LLM Provider (gemini or openai)
    llm_provider: str

    # Zoom RTMS
    zoom_client_id: str
    zoom_client_secret: str

    # Deepgram options
    # nova-3-general: measured 2026-09-15 against this repo's own clip +
    # ground-truth transcript at ~22% relative WER reduction over
    # nova-2-general, plus nova-2 flatly rejects keyterm prompting (the
    # default deepgram_keyterms_enabled=True then burns 6 failed WebSocket
    # connection attempts before falling back to 0 keyterms on every
    # connect). See experiments/20260915_asr_model_*.json and
    # technical_term_recall.py.
    deepgram_model: str = "nova-3-general"
    deepgram_language: str = "en"
    deepgram_interim_results: bool = True
    deepgram_smart_format: bool = True
    # 300ms empirically validated: cuts avg end-to-end latency from ~8s to
    # ~5s (max_interim_duration is the bigger lever, but both compound).
    deepgram_endpointing: int = 300
    # NOTE: Deepgram rejects utterance_end_ms below 1000ms (400 error).
    deepgram_utterance_end_ms: int | None = 1000
    deepgram_vad_events: bool | None = None
    # Force-finalize (soft-final) an in-progress utterance after this many
    # seconds even without a natural pause, so continuous fluent speech
    # doesn't stall translation indefinitely. None disables it. 2.5s
    # empirically brings avg end-to-end latency to ~5s (from ~8s at 4.0s),
    # at the cost of more mid-sentence fragment translations.
    deepgram_max_interim_duration: float | None = 2.5
    # Bias ASR toward `dictionary_path` terms via Deepgram Keyterm Prompting
    # (nova-3 models only). Fixes domain-term errors at the transcription
    # source instead of relying on the LLM to guess-correct them from
    # context after the fact.
    deepgram_keyterms_enabled: bool = True
    # Deepgram caps keyterm prompting at ~100 terms / 500 tokens per request.
    deepgram_max_keyterms: int = 100
    zoom_webhook_port: int = 8080
    zoom_webhook_path: str = "/webhook"

    # Gemini
    google_api_key: str | None = None
    # gemini-2.x family returns 404 "no longer available to new users" as
    # of 2026-07-23 (confirmed via direct API test, not key-specific).
    gemini_model: str = "gemini-3.1-flash-lite"
    # 0 disables extended "thinking" for lower latency. Empirically, gemini-3.x
    # models carry multi-second thinking overhead that isn't fully suppressible
    # yet; gemini-2.5-flash with thinking_budget=0 measured ~700-1000ms TTFT.
    gemini_thinking_budget: int | None = 0

    # OpenAI
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    # Translation settings
    source_language: str = "en"
    target_language: str = "ja"
    context_window_size: int = 3
    # Deeper than a single burst of soft-finalized fragments (see
    # deepgram_max_interim_duration) so a backlog has enough queued material
    # for translation_worker batching to coalesce into fewer, larger LLM
    # calls instead of the oldest items being evicted outright.
    translation_queue_size: int = 20
    # Parallel translation workers pulling from the shared queue. More workers
    # only help throughput up to the provider's RPM cap (see gemini_rpm_limit).
    translation_workers: int = 4
    # When a worker becomes free, it drains up to this many queued fragments
    # into a single LLM call (still capped by translation_batch_max_chars)
    # instead of firing one request per fragment. This is what keeps
    # request volume bounded under backlog (e.g. continuous speech
    # soft-finalizing every deepgram_max_interim_duration seconds against a
    # low gemini_rpm_limit) and gives the model a fuller, more coherent span
    # of text to translate instead of many disjoint mid-sentence fragments.
    translation_batch_max_items: int = 6
    # Character budget (of source text) per coalesced batch, so a single
    # request doesn't grow large enough to risk truncation against
    # max_output_tokens or add noticeable latency to the head-of-queue item.
    translation_batch_max_chars: int = 400
    # Client-side rate limit (requests/minute) to stay under the provider's
    # quota instead of firing requests that will 429. Free-tier Gemini flash
    # models measured ~10-15 RPM; default is a conservative margin under that.
    # Raise this once a paid-tier key is available (Tier 1 is ~300 RPM).
    # <= 0 disables the self-throttle entirely (see RateLimiter) -- only
    # the provider's own server-side limit and this pipeline's
    # retry-once-then-drop handling remain as backstops.
    gemini_rpm_limit: int = 9
    # Hard ceiling on a single translation call. Observed in practice:
    # google-genai's async streaming call can hang indefinitely with no
    # error, which would otherwise permanently strand a worker.
    translation_timeout: float = 15.0
    # Experimental (h-masking-holdback, research_agent/state/hypotheses.json):
    # withhold the last N words of a not-yet-utterance-ending accumulated
    # ASR hypothesis from translation, per Arivazhagan et al.'s masking
    # strategy for reducing re-translation erasure/flicker. The held-back
    # words are still recorded in `_utterance_target_text` (so they're
    # included once the utterance actually ends, or pushed off the tail by
    # later words on the next continuation) -- only what gets *sent to the
    # translator this round* is truncated. 0 (default) disables masking
    # entirely, preserving today's behavior.
    masking_holdback_words: int = 0
    # Experimental (h-continuation-context-anchor,
    # research_agent/state/hypotheses.json): when retranslating a
    # continuation batch of an in-progress utterance, pass the utterance's
    # own most-recently-emitted translation to the LLM as an explicit
    # <prior_translation> anchor, instructing it to preserve/extend that
    # text rather than retranslating from scratch. False (default) preserves
    # today's behavior (no anchor passed).
    anchor_continuation_translation: bool = False
    # Experimental (h-localagreement-asr-commit,
    # research_agent/state/hypotheses.json): instead of only soft-finalizing
    # an in-progress utterance on the fixed `deepgram_max_interim_duration`
    # timer, also commit (and make eligible for translation) any word prefix
    # that two consecutive Deepgram interim hypotheses agree on
    # (LocalAgreement-2, per whisper-streaming) as soon as that agreement is
    # observed. False (default) preserves today's behavior (timer-only
    # commits, per DeepgramTranscriber's own consumed-word-count tracking).
    localagreement_commit_enabled: bool = False
    # Experimental (h-asr-confidence-early-commit,
    # research_agent/state/hypotheses.json): reuse the existing periodic
    # force-finalize check (DeepgramTranscriber._force_finalize_loop) but
    # also allow it to soft-finalize BEFORE `deepgram_max_interim_duration`
    # once an in-progress utterance has run at least
    # `asr_confidence_early_commit_min_elapsed` seconds AND Deepgram's own
    # confidence for the current pending interim is at or above this
    # threshold. None (default) disables early commit entirely, preserving
    # today's timer-only behavior. Deliberately reuses the SAME 0.5s
    # periodic-check cadence LocalAgreement-2 does NOT use (that hypothesis
    # instead commits on every interim message and was found, twice, to
    # fragment translation calls badly) -- this is meant to move the
    # existing timer earlier for confidently-transcribed speech, not to
    # introduce a new high-frequency commit path.
    asr_confidence_early_commit_threshold: float | None = None
    # Minimum seconds an utterance must have been accumulating before a
    # high-confidence early commit is allowed to fire, even if confidence
    # is already high on the very first interim (which is common and not a
    # meaningful signal this early -- Deepgram's short interims can be
    # confidently wrong about where a word will end up).
    asr_confidence_early_commit_min_elapsed: float = 2.0
    # Experimental (caption-readability engineering track, 2026-09-15):
    # the 10-minute real-lecture stress test found that fixing translation
    # backlog (gemini_rpm_limit) closes most of the *latency* gap but
    # leaves reading-speed (CPS) compliance mostly unsolved on dense
    # technical content -- a verbatim translation of a long, information-
    # dense utterance is simply too much text to read in the time the
    # speaker took to say it. When True, each batch's translation prompt
    # includes an approximate target character budget derived from the
    # batch's own source speech duration (see pipeline.py's
    # _reading_speed_char_budget), asking the model to compress rather
    # than translate verbatim when the source is dense -- trading some
    # literalness for a caption a viewer can actually finish reading.
    # False (default) preserves today's verbatim-translation behavior.
    reading_speed_budget_translation: bool = False
    # Target output reading pace (raw JA characters/second, NOT the
    # kanji-weighted CPS readability_metrics.py scores against) used to
    # compute that budget. Deliberately looser than Netflix's 4.0
    # *weighted* CPS standard -- this is a soft prompt hint, not a hard
    # cap, and translators already tend to run over an aggressive budget.
    reading_speed_chars_per_sec: float = 6.0
    # Experimental (h-compression-actions-prompt-instruction,
    # research_agent/state/hypotheses.json): a paper-inspired explicit
    # compression instruction (SENTENCE_CUT/DROP/PARTIAL_SUMMARIZATION/
    # PRONOMINALIZATION, with an explicit anti-content-drop guardrail)
    # appended to the system prompt, as an alternative to
    # reading_speed_budget_translation's implicit character-budget nudge.
    # Previously only tested via a Gemini-only replay
    # (compression_actions_replay.py) against already-recorded ASR segments
    # because live Deepgram access was unavailable that cycle -- this flag
    # wires the same instruction text into the live pipeline for a real
    # end-to-end A/B. False (default) preserves today's behavior.
    compression_actions_prompt_enabled: bool = False
    # Experimental (h-monotonic-chunkwise-prompt-enja,
    # research_agent/state/hypotheses.json): for EN->JA specifically
    # (a distant word-order pair), instruct the translator to prefer
    # source-word-order-preserving phrasing over natural fluency for any
    # not-yet-utterance-final batch, on the theory (Makinae et al. 2024)
    # that this reduces how much clause-final material the model needs to
    # wait for before producing plausible output. The eventual
    # utterance-final batch is unaffected and still gets a natural
    # rewrite. False (default) preserves today's always-natural-phrasing
    # behavior.
    monotonic_interim_translation_enabled: bool = False

    # Dictionary
    dictionary_path: Path | None = None
    # Above this many entries, stop dumping the full dictionary into the
    # cached LLM system prompt and switch to per-request relevance-based
    # retrieval instead, so prompt size/cache cost don't grow with the
    # dictionary (see LLMTranslator._is_dynamic_mode).
    dictionary_dynamic_threshold: int = 80
    # Max terms injected per request once in dynamic mode.
    dictionary_dynamic_limit: int = 30
    # Curated glossary packs (dictionaries/domains/<name>.csv), loaded
    # before dictionary_path so session-specific terms override the pack.
    domain_packs: list[str] = field(default_factory=list)
    domain_packs_dir: Path = DEFAULT_DOMAIN_PACKS_DIR
    # Path to a slide deck (.pdf/.pptx) or video file to auto-extract
    # supplementary terminology from at session start, the way a human
    # interpreter pre-reads material before a talk (see preload.auto_preload).
    # None disables auto-preload entirely.
    preload_source: Path | None = None
    preload_max_terms: int = 80

    @classmethod
    def from_env(
        cls, env_file: Path | None = None, *, require_zoom: bool = True
    ) -> "Config":
        """Load configuration from environment variables.

        Args:
            env_file: Optional path to .env file
            require_zoom: Whether Zoom RTMS credentials are required

        Returns:
            Config instance
        """
        # override=True: .env must win over a stale same-named var already
        # in the shell/OS environment. python-dotenv defaults to NOT
        # overriding, which silently makes .env edits a no-op whenever a
        # leftover shell env var (from an earlier export, another project,
        # CI, etc.) shadows it -- confirmed happening here for
        # OPENAI_API_KEY, which made every OpenAI call use a stale key
        # with no visible error pointing at the cause.
        if env_file:
            load_dotenv(env_file, override=True)
        else:
            load_dotenv(override=True)

        deepgram_api_key = os.getenv("DEEPGRAM_API_KEY", "")
        if not deepgram_api_key:
            raise ValueError("DEEPGRAM_API_KEY is required")

        def _get_bool_env(name: str, default: bool) -> bool:
            value = os.getenv(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        def _get_optional_int_env(name: str) -> int | None:
            value = os.getenv(name)
            if value is None:
                return None
            value = value.strip()
            if not value:
                return None
            return int(value)

        def _get_optional_bool_env(name: str) -> bool | None:
            value = os.getenv(name)
            if value is None:
                return None
            value = value.strip()
            if not value:
                return None
            return value.lower() in {"1", "true", "yes", "on"}

        # Zoom RTMS credentials
        zoom_client_id = os.getenv("ZOOM_CLIENT_ID", "")
        zoom_client_secret = os.getenv("ZOOM_CLIENT_SECRET", "")
        if require_zoom and (not zoom_client_id or not zoom_client_secret):
            raise ValueError("ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET are required")

        llm_provider = os.getenv("LLM_PROVIDER", "gemini").lower()

        google_api_key = os.getenv("GOOGLE_API_KEY")
        openai_api_key = os.getenv("OPENAI_API_KEY")

        if llm_provider == "gemini" and not google_api_key:
            raise ValueError("GOOGLE_API_KEY is required when using Gemini")
        if llm_provider == "openai" and not openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when using OpenAI")

        # Dictionary path
        dictionary_path_str = os.getenv("DICTIONARY_PATH")
        dictionary_path = Path(dictionary_path_str) if dictionary_path_str else None

        domain_packs_str = os.getenv("DOMAIN_PACKS", "")
        domain_packs = [p.strip() for p in domain_packs_str.split(",") if p.strip()]
        domain_packs_dir = Path(
            os.getenv("DOMAIN_PACKS_DIR", str(DEFAULT_DOMAIN_PACKS_DIR))
        )

        preload_source_str = os.getenv("PRELOAD_SOURCE", "").strip()
        preload_source = Path(preload_source_str) if preload_source_str else None

        # Unset -> default to 0 (thinking disabled). Explicitly set to an empty
        # string -> None (omit the param, for models that reject it).
        gemini_thinking_budget_env = os.getenv("GEMINI_THINKING_BUDGET")
        gemini_thinking_budget = (
            0
            if gemini_thinking_budget_env is None
            else _get_optional_int_env("GEMINI_THINKING_BUDGET")
        )

        return cls(
            deepgram_api_key=deepgram_api_key,
            deepgram_model=os.getenv("DEEPGRAM_MODEL", "nova-3-general"),
            deepgram_language=os.getenv("DEEPGRAM_LANGUAGE", "en"),
            deepgram_interim_results=_get_bool_env("DEEPGRAM_INTERIM_RESULTS", True),
            deepgram_smart_format=_get_bool_env("DEEPGRAM_SMART_FORMAT", True),
            deepgram_endpointing=int(os.getenv("DEEPGRAM_ENDPOINTING", "300")),
            deepgram_utterance_end_ms=_get_optional_int_env(
                "DEEPGRAM_UTTERANCE_END_MS"
            ),
            deepgram_vad_events=_get_optional_bool_env("DEEPGRAM_VAD_EVENTS"),
            deepgram_keyterms_enabled=_get_bool_env("DEEPGRAM_KEYTERMS_ENABLED", True),
            deepgram_max_keyterms=int(os.getenv("DEEPGRAM_MAX_KEYTERMS", "100")),
            deepgram_max_interim_duration=(
                float(v)
                if (v := os.getenv("DEEPGRAM_MAX_INTERIM_DURATION")) is not None
                and v.strip()
                else 2.5
            ),
            llm_provider=llm_provider,
            zoom_client_id=zoom_client_id,
            zoom_client_secret=zoom_client_secret,
            zoom_webhook_port=int(os.getenv("ZOOM_WEBHOOK_PORT", "8080")),
            zoom_webhook_path=os.getenv("ZOOM_WEBHOOK_PATH", "/webhook"),
            google_api_key=google_api_key,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            gemini_thinking_budget=gemini_thinking_budget,
            openai_api_key=openai_api_key,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            source_language=os.getenv("SOURCE_LANGUAGE", "en"),
            target_language=os.getenv("TARGET_LANGUAGE", "ja"),
            context_window_size=int(os.getenv("CONTEXT_WINDOW_SIZE", "3")),
            translation_queue_size=int(os.getenv("TRANSLATION_QUEUE_SIZE", "20")),
            translation_workers=int(os.getenv("TRANSLATION_WORKERS", "4")),
            translation_batch_max_items=int(
                os.getenv("TRANSLATION_BATCH_MAX_ITEMS", "6")
            ),
            translation_batch_max_chars=int(
                os.getenv("TRANSLATION_BATCH_MAX_CHARS", "400")
            ),
            gemini_rpm_limit=int(os.getenv("GEMINI_RPM_LIMIT", "9")),
            translation_timeout=float(os.getenv("TRANSLATION_TIMEOUT", "15.0")),
            masking_holdback_words=int(os.getenv("MASKING_HOLDBACK_WORDS", "0")),
            anchor_continuation_translation=(
                os.getenv("ANCHOR_CONTINUATION_TRANSLATION", "0") == "1"
            ),
            localagreement_commit_enabled=(
                os.getenv("LOCALAGREEMENT_COMMIT_ENABLED", "0") == "1"
            ),
            asr_confidence_early_commit_threshold=(
                float(v)
                if (v := os.getenv("ASR_CONFIDENCE_EARLY_COMMIT_THRESHOLD"))
                is not None
                else None
            ),
            asr_confidence_early_commit_min_elapsed=float(
                os.getenv("ASR_CONFIDENCE_EARLY_COMMIT_MIN_ELAPSED", "2.0")
            ),
            reading_speed_budget_translation=(
                os.getenv("READING_SPEED_BUDGET_TRANSLATION", "0") == "1"
            ),
            reading_speed_chars_per_sec=float(
                os.getenv("READING_SPEED_CHARS_PER_SEC", "6.0")
            ),
            compression_actions_prompt_enabled=(
                os.getenv("COMPRESSION_ACTIONS_PROMPT_ENABLED", "0") == "1"
            ),
            monotonic_interim_translation_enabled=(
                os.getenv("MONOTONIC_INTERIM_TRANSLATION_ENABLED", "0") == "1"
            ),
            dictionary_path=dictionary_path,
            dictionary_dynamic_threshold=int(
                os.getenv("DICTIONARY_DYNAMIC_THRESHOLD", "80")
            ),
            dictionary_dynamic_limit=int(
                os.getenv("DICTIONARY_DYNAMIC_LIMIT", "30")
            ),
            domain_packs=domain_packs,
            domain_packs_dir=domain_packs_dir,
            preload_source=preload_source,
            preload_max_terms=int(os.getenv("PRELOAD_MAX_TERMS", "80")),
        )
