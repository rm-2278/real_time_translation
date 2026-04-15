"""Translation microservice entrypoint."""

from __future__ import annotations

import asyncio

import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel, Field

from translator_service.fast_translator import FastTranslator
from translator_service.zoom_caption import ZoomCaptionClient
from translator_service.queue_manager import (
    TranslationQueueConfig,
    TranslationQueueManager,
    TranslationRequest,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranslationServiceConfig:
    llm_provider: str
    google_api_keys: list[str]
    google_api_keys_free: list[str]
    openai_api_key: str | None
    gemini_model: str
    openai_model: str
    source_language: str
    target_language: str
    dictionary_path: Path | None
    ws_publish_url: str
    http_timeout: float
    zoom_caption_url: str | None
    zoom_caption_lang: str | None
    # Queue configuration
    num_workers: int
    max_queue_size: int
    lag_threshold_seconds: float
    enable_summarization: bool

    @staticmethod
    def from_env() -> TranslationServiceConfig:
        llm_provider = os.getenv("LLM_PROVIDER", "gemini").lower()
        
        def _get_keys(env_name: str) -> list[str]:
            val = os.getenv(env_name, "")
            if not val:
                return []
            return [k.strip() for k in val.split(",") if k.strip()]

        google_api_keys = _get_keys("GOOGLE_API_KEY")
        google_api_keys_free = _get_keys("GOOGLE_API_KEY_FREE")
        openai_api_key = os.getenv("OPENAI_API_KEY")
        
        if llm_provider == "gemini" and not google_api_keys and not google_api_keys_free:
            raise ValueError("GOOGLE_API_KEY or GOOGLE_API_KEY_FREE is required when using Gemini")
        if llm_provider == "openai" and not openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when using OpenAI")

        dictionary_path = os.getenv("DICTIONARY_PATH")
        return TranslationServiceConfig(
            llm_provider=llm_provider,
            google_api_keys=google_api_keys,
            google_api_keys_free=google_api_keys_free,
            openai_api_key=openai_api_key,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            source_language=os.getenv("SOURCE_LANGUAGE", "en"),
            target_language=os.getenv("TARGET_LANGUAGE", "ja"),
            dictionary_path=Path(dictionary_path) if dictionary_path else None,
            ws_publish_url=os.getenv("WS_PUBLISH_URL", "http://ws:8000/publish"),
            http_timeout=float(os.getenv("HTTP_TIMEOUT", "10")),
            zoom_caption_url=os.getenv("ZOOM_CAPTION_URL"),
            zoom_caption_lang=os.getenv("ZOOM_CAPTION_LANG"),
            # Queue configuration
            num_workers=int(os.getenv("TRANSLATION_WORKERS", "4")),
            max_queue_size=int(os.getenv("TRANSLATION_QUEUE_SIZE", "50")),
            lag_threshold_seconds=float(os.getenv("LAG_THRESHOLD_SECONDS", "5.0")),
            enable_summarization=os.getenv("ENABLE_SUMMARIZATION", "true").lower()
            in {"1", "true", "yes", "on"},
        )


class TranslateRequest(BaseModel):
    text: str
    context: list[str] = Field(default_factory=list)
    is_final: bool = True
    ts: float | None = None
    session_id: str | None = None


class TranslateResponse(BaseModel):
    translated: str
    kept_terms: list[str] = Field(default_factory=list)
    is_final: bool
    ts: float


class SentenceBuffer:
    """Buffer to accumulate ASR chunks into complete sentences."""

    def __init__(self, max_chars: int = 300):
        self._buffer: str = ""
        self._first_ts: float | None = None
        self._max_chars = max_chars
        self._context: list[str] = []
        self._session_id: str | None = None
        self._lock = asyncio.Lock()

    def _ends_with_sentence(self, text: str) -> bool:
        """Check if text ends with sentence-ending punctuation."""
        text = text.rstrip()
        if not text:
            return False
        return text[-1] in ".!?。！？"

    async def add(
        self, text: str, ts: float, context: list[str], session_id: str | None
    ) -> tuple[str, float, list[str], str | None] | None:
        """Add text to buffer. Returns (merged_text, ts, context, session_id) when sentence is complete."""
        async with self._lock:
            if self._first_ts is None:
                self._first_ts = ts
            self._context = context
            self._session_id = session_id

            # Append to buffer
            if self._buffer:
                self._buffer += " " + text.strip()
            else:
                self._buffer = text.strip()

            # Flush if sentence is complete OR buffer is too long
            should_flush = (
                self._ends_with_sentence(self._buffer)
                or len(self._buffer) >= self._max_chars
            )

            if should_flush:
                result = (self._buffer, self._first_ts, self._context, self._session_id)
                self._buffer = ""
                self._first_ts = None
                return result

            return None

    async def flush(self) -> tuple[str, float, list[str], str | None] | None:
        """Force flush the buffer."""
        async with self._lock:
            if not self._buffer:
                return None
            result = (self._buffer, self._first_ts or time.time(), self._context, self._session_id)
            self._buffer = ""
            self._first_ts = None
            return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - initialize and cleanup resources.
    
    MODIFIED 2026-02-04: Added queue manager initialization for parallel processing.
    """
    logging.basicConfig(level=logging.INFO)
    config = TranslationServiceConfig.from_env()
    
    # Determine model based on provider
    model = (
        config.gemini_model if config.llm_provider == "gemini" else config.openai_model
    )

    translator = FastTranslator(
        provider=config.llm_provider,  # type: ignore[arg-type]
        google_api_keys=config.google_api_keys,
        google_api_keys_free=config.google_api_keys_free,
        api_key=config.openai_api_key,
        model=model,
        source_language=config.source_language,
        target_language=config.target_language,
        dictionary_path=config.dictionary_path,
    )
    await translator.prepare()

    http_client = httpx.AsyncClient(timeout=config.http_timeout)
    
    # Initialize Zoom caption client if URL is configured
    zoom_caption: ZoomCaptionClient | None = None
    if config.zoom_caption_url:
        zoom_caption = ZoomCaptionClient(
            caption_url=config.zoom_caption_url,
            http_client=http_client,
            lang=config.zoom_caption_lang,
        )
        await zoom_caption.sync_seq()
        logger.info("Zoom caption client initialized")

    # Create translation function for queue workers
    # This function is called by each worker when processing a request
    async def do_translate(
        text: str,
        context: list[str],
        is_final: bool,
        ts: float,
        session_id: str | None,
    ) -> None:
        # Perform actual translation via LLMTranslator
        output = await translator.translate(
            text,
            context_lines=context,
            update_context=False,
        )
        # Build payload to publish to WebSocket service
        payload = {
            "type": "translation",
            "src": text,
            "translated": output.latest_slide,
            "kept_terms": output.kept_terms,
            "is_final": is_final,
            "ts": ts,
            "session_id": session_id,
            "lag": time.time() - ts,
        }
        await _publish_translation(http_client, config.ws_publish_url, payload)

        # Send caption to Zoom if configured and this is a final result
        if zoom_caption and is_final:
            await zoom_caption.send_caption(output.latest_slide)

    # Create summarize function (for lag recovery - batch multiple texts)
    async def do_summarize(texts: list[str]) -> str:
        return await translator.summarize_texts(texts)

    # Initialize queue manager with parallel workers
    # This is the key change that prevents lag accumulation
    queue_config = TranslationQueueConfig(
        num_workers=config.num_workers,       # Default: 6 workers
        max_queue_size=config.max_queue_size, # Default: 50 (drops if exceeded)
        lag_threshold_seconds=config.lag_threshold_seconds,  # Default: 3.0s
    )
    queue_manager = TranslationQueueManager(
        config=queue_config,
        translate_fn=do_translate,  # Inject translation function
        summarize_fn=do_summarize if config.enable_summarization else None,
    )
    await queue_manager.start()  # Start N worker tasks

    # Store in app.state for endpoint access
    app.state.config = config
    app.state.translator = translator
    app.state.http_client = http_client
    app.state.zoom_caption = zoom_caption
    app.state.queue_manager = queue_manager
    try:
        yield
    finally:
        # Cleanup on shutdown
        await queue_manager.stop()
        await http_client.aclose()


app = FastAPI(lifespan=lifespan)

# Add CORS for browser access to /stats endpoint
# Needed because captions.html fetches stats from different origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # Allow all origins (internal use)
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


async def _publish_translation(
    http_client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
) -> None:
    try:
        await http_client.post(url, json=payload)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to publish translation update")


@app.post("/translate", response_model=TranslateResponse)
async def translate(request: TranslateRequest) -> TranslateResponse:
    """Main translation endpoint - enqueues request for parallel processing."""
    queue_manager: TranslationQueueManager = app.state.queue_manager

    ts = request.ts or time.time()

    # Create translation request and enqueue (non-blocking)
    translation_request = TranslationRequest(
        text=request.text,
        context=request.context,
        is_final=request.is_final,
        ts=ts,
        session_id=request.session_id,
    )
    await queue_manager.enqueue(translation_request)

    # Return immediately - actual translation happens in worker
    return TranslateResponse(
        translated="[queued]",  # Placeholder - real result sent via WebSocket
        kept_terms=[],
        is_final=request.is_final,
        ts=ts,
    )


@app.post("/translate_sync", response_model=TranslateResponse)
async def translate_sync(request: TranslateRequest) -> TranslateResponse:
    """Synchronous translation endpoint for testing or single requests.
    
    Unlike /translate, this waits for translation to complete and returns result.
    Use for testing or when you need the result directly.
    """
    translator: FastTranslator = app.state.translator  # type: ignore[assignment]
    config: TranslationServiceConfig = app.state.config
    http_client: httpx.AsyncClient = app.state.http_client
    zoom_caption: ZoomCaptionClient | None = app.state.zoom_caption

    output = await translator.translate(
        request.text,
        context_lines=request.context,
        update_context=False,
    )

    ts = request.ts or time.time()
    payload = {
        "type": "translation",
        "src": request.text,
        "translated": output.latest_slide,
        "kept_terms": output.kept_terms,
        "is_final": request.is_final,
        "ts": ts,
        "session_id": request.session_id,
    }
    await _publish_translation(http_client, config.ws_publish_url, payload)
    
    # Send caption to Zoom if configured and this is a final result
    if zoom_caption and request.is_final:
        await zoom_caption.send_caption(output.latest_slide)

    return TranslateResponse(
        translated=output.latest_slide,
        kept_terms=output.kept_terms,
        is_final=request.is_final,
        ts=ts,
    )


@app.get("/stats")
async def stats() -> dict[str, Any]:
    """Get translation queue and API key statistics for monitoring.
    
    Returns:
        - processed: Number of successful translations
        - dropped: Number of dropped requests (queue full or stale)
        - summarized: Number of texts combined via summarization
        - current_lag: Current lag in seconds
        - queue_size: Items currently waiting in queue
        - pending_for_summary: Items waiting for batch summarization
        - api_keys: Statistics from the KeyManager (RPM per key, health, etc.)
    """
    queue_manager: TranslationQueueManager = app.state.queue_manager
    translator: FastTranslator = app.state.translator
    
    stats_data = queue_manager.stats
    stats_data["api_keys"] = translator.key_manager_stats
    return stats_data


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
