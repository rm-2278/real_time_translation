"""Key manager for API rotation and rate limit handling."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Literal

logger = logging.getLogger(__name__)


class Tier(Enum):
    FREE = "free"
    PAID = "paid"


@dataclass
class APIKey:
    key: str
    tier: Tier
    rpm_limit: int
    disabled_until: float = 0
    consecutive_errors: int = 0
    last_used: float = 0

    @property
    def is_healthy(self) -> bool:
        return time.time() >= self.disabled_until


class KeyManager:
    """Manages multiple API keys with rotation and rate limiting."""

    def __init__(self, paid_keys: list[str] | None = None, free_keys: list[str] | None = None):
        self._paid_keys: list[APIKey] = []
        self._free_keys: list[APIKey] = []
        self._request_timestamps: list[float] = []
        self._key_request_timestamps: dict[str, list[float]] = {}
        
        if paid_keys:
            for k in paid_keys:
                key_obj = APIKey(key=k, tier=Tier.PAID, rpm_limit=150)
                self._paid_keys.append(key_obj)
                self._key_request_timestamps[k] = []
                
        if free_keys:
            for k in free_keys:
                key_obj = APIKey(key=k, tier=Tier.FREE, rpm_limit=15)
                self._free_keys.append(key_obj)
                self._key_request_timestamps[k] = []

        self._paid_index = 0
        self._free_index = 0
        self._lock = asyncio.Lock()
        
        if not self._paid_keys and not self._free_keys:
            logger.warning("No API keys configured for KeyManager")

    @property
    def _all_keys(self) -> list[APIKey]:
        return self._paid_keys + self._free_keys

    @property
    def has_keys(self) -> bool:
        return len(self._all_keys) > 0

    @property
    def stats(self) -> dict[str, Any]:
        """Get statistics about key usage and RPM."""
        now = time.time()
        self._request_timestamps = [t for t in self._request_timestamps if now - t < 60]
        
        key_stats = []
        for key in self._all_keys:
            ts_list = self._key_request_timestamps.get(key.key, [])
            ts_list[:] = [t for t in ts_list if now - t < 60]
            
            key_stats.append({
                "key_id": key.key[:8] + "...",
                "tier": key.tier.value,
                "healthy": key.is_healthy,
                "consecutive_errors": key.consecutive_errors,
                "current_rpm": len(ts_list),
                "rpm_limit": key.rpm_limit
            })

        return {
            "total_rpm": len(self._request_timestamps),
            "keys": key_stats
        }

    async def get_key(self) -> tuple[APIKey, float] | tuple[None, None]:
        """Get the next healthy API key and required wait time, prioritizing PAID keys.
        
        This method is non-blocking (doesn't sleep). It calculates the required
        wait time to respect RPM limits and 'reserves' the slot by updating
        the key's last_used timestamp to the projected future usage time.
        """
        async with self._lock:
            # 1. Try Paid Keys first
            if self._paid_keys:
                start_index = self._paid_index
                while True:
                    key = self._paid_keys[self._paid_index]
                    self._paid_index = (self._paid_index + 1) % len(self._paid_keys)

                    if key.is_healthy:
                        wait_time = self._reserve_key(key)
                        return key, wait_time

                    if self._paid_index == start_index:
                        break  # All paid keys exhausted/unhealthy

            # 2. Fallback to Free Keys
            if self._free_keys:
                start_index = self._free_index
                while True:
                    key = self._free_keys[self._free_index]
                    self._free_index = (self._free_index + 1) % len(self._free_keys)

                    if key.is_healthy:
                        wait_time = self._reserve_key(key)
                        return key, wait_time

                    if self._free_index == start_index:
                        break  # All free keys exhausted/unhealthy

            logger.warning("All API keys are currently disabled due to rate limits")
            return None, None

    def _reserve_key(self, key: APIKey) -> float:
        """Helper to reserve a slot and return the required wait_time (no sleeping)."""
        now = time.time()
        interval = 60.0 / key.rpm_limit
        
        # Calculate when this key can next be used
        # If last_used was in the past, it's max(now, last_used + interval)
        next_available = key.last_used + interval
        
        if now >= next_available:
            # Key is ready now
            wait_time = 0.0
            key.last_used = now
        else:
            # Key needs to wait
            wait_time = next_available - now
            key.last_used = next_available
            
        self._request_timestamps.append(key.last_used)
        self._key_request_timestamps[key.key].append(key.last_used)
        return wait_time

    def report_error(self, key_str: str, status_code: int):
        """Report an error for a specific key."""
        for key in self._all_keys:
            if key.key == key_str:
                if status_code == 429:
                    key.consecutive_errors += 1
                    backoff = min(60 * 5, 30 * (2 ** (key.consecutive_errors - 1)))
                    key.disabled_until = time.time() + backoff
                    logger.warning(
                        "Rate limit hit for %s tier key. Disabling for %d seconds.",
                        key.tier.value, backoff
                    )
                break

    def report_success(self, key_str: str):
        """Report success for a specific key to reset error state."""
        for key in self._all_keys:
            if key.key == key_str:
                key.consecutive_errors = 0
                break
