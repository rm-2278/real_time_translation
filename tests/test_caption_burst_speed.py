"""Regression tests for the 2026-10-13 review: a delayed card, then cards flashed."""

from __future__ import annotations

from real_time_translation.captions.subtitle_formatter import (
    BURST_UNITS_PER_SEC,
    _RawUtterance,
    format_readable,
    weighted_len,
)
from real_time_translation.experiments.caption_issue_metrics import (
    caption_anomalies,
)


def _speed(cue) -> float:
    return weighted_len(cue.text.replace("\n", "")) / (cue.end - cue.start)


def test_simultaneous_short_utterances_become_one_card() -> None:
    cues = format_readable(
        [
            _RawUtterance(10.7, "はい、見えています。"),
            _RawUtterance(10.7, "皆さん、こんにちは。"),
        ]
    )
    assert len(cues) == 1
    assert "見えています" in cues[0].text and "こんにちは" in cues[0].text


def test_long_burst_never_flashes_faster_than_ceiling() -> None:
    long_text = (
        "現在はGoogle Researchのシニアリサーチサイエンティストです。"
        "また、松尾・岩澤研究室の招待に感謝します。議論できることを嬉しく思います。"
    )
    cues = format_readable(
        [_RawUtterance(22.1, long_text), _RawUtterance(22.5, long_text)]
    )
    assert len(cues) > 3
    assert all(_speed(c) <= BURST_UNITS_PER_SEC + 1e-6 for c in cues)


def test_anomaly_gate_flags_fast_cards_and_bursts() -> None:
    ok = {
        "over_burst_cards": 0,
        "peak_units_per_sec": 5,
        "max_cards_in_3s": 2,
        "midsentence_blank_gaps": 0,
    }
    assert caption_anomalies(ok) == []
    bad = {
        "over_burst_cards": 2,
        "peak_units_per_sec": 30,
        "max_cards_in_3s": 5,
        "midsentence_blank_gaps": 0,
    }
    assert len(caption_anomalies(bad)) == 2


def test_midsentence_card_is_held_until_its_continuation() -> None:
    from real_time_translation.captions.subtitle_formatter import (
        Cue,
        _hold_through_continuations,
    )

    cues = [
        Cue(0.0, 2.0, "研究を始めましたが、"),
        Cue(2.6, 5.0, "続きです。"),
        Cue(5.4, 7.0, "別の話。"),
    ]
    out = _hold_through_continuations(cues)
    assert out[0].end == 2.6  # mid-sentence: no blank gap
    assert out[1].end == 5.0  # sentence ended: gap kept
