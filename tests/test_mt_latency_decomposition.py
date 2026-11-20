"""Synthetic batch-list examples for mt_latency_decomposition.py's
content_ready_wallclock exact-match / nearest-interim-fallback / no-prior
cases (h-content-ready-wallclock-nearest-interim-fallback, cycle 95)."""

from real_time_translation.experiments.mt_latency_decomposition import (
    batch_latencies,
)


def test_exact_match_unchanged():
    # An asr_interim reports exactly this batch's asr_end_time -> exact
    # match, same behavior as before this cycle's fallback was added.
    events = [
        {"kind": "asr_interim", "playback_offset": 0.0, "asr_end_time": 3.0},
        {
            "kind": "translation_partial",
            "playback_offset": 2.0,
            "asr_start_time": 0.0,
            "asr_end_time": 3.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 2.5,
            "asr_start_time": 0.0,
            "asr_end_time": 3.0,
        },
    ]
    batches = batch_latencies(events)
    assert len(batches) == 1
    b = batches[0]
    assert b.content_ready_wallclock == 0.0
    assert b.content_ready_is_exact is True
    assert b.queue_wait_s == 2.0
    assert b.stream_duration_s == 0.5


def test_fallback_to_nearest_prior_interim():
    # No asr_interim reports asr_end_time=4.0 exactly, but one reports
    # asr_end_time=3.4 (the nearest one strictly before) -> fallback match,
    # content_ready_wallclock taken from that earlier interim instead of
    # None.
    events = [
        {"kind": "asr_interim", "playback_offset": 0.0, "asr_end_time": 1.0},
        {"kind": "asr_interim", "playback_offset": 1.0, "asr_end_time": 3.4},
        {
            "kind": "translation_partial",
            "playback_offset": 2.0,
            "asr_start_time": 0.0,
            "asr_end_time": 4.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 2.5,
            "asr_start_time": 0.0,
            "asr_end_time": 4.0,
        },
    ]
    batches = batch_latencies(events)
    assert len(batches) == 1
    b = batches[0]
    assert b.content_ready_wallclock == 1.0
    assert b.content_ready_is_exact is False
    assert b.queue_wait_s == 1.0  # 2.0 - 1.0, not None


def test_no_prior_interim_stays_none():
    # No asr_interim event at all reports any asr_end_time <= this batch's
    # own asr_end_time (first batch of a run / schema gap) -> remains
    # unrecoverable, queue_wait_s stays None, same as before this cycle.
    events = [
        {
            "kind": "translation_partial",
            "playback_offset": 1.0,
            "asr_start_time": 0.0,
            "asr_end_time": 5.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 1.2,
            "asr_start_time": 0.0,
            "asr_end_time": 5.0,
        },
    ]
    batches = batch_latencies(events)
    assert len(batches) == 1
    b = batches[0]
    assert b.content_ready_wallclock is None
    assert b.content_ready_is_exact is None
    assert b.queue_wait_s is None


def test_fallback_not_used_when_only_later_interim_exists():
    # An asr_interim with a LARGER asr_end_time than this batch's own
    # should never be used as a fallback (it is not "content ready
    # earlier", it is content from later in the stream) -> stays
    # unrecoverable.
    events = [
        {"kind": "asr_interim", "playback_offset": 5.0, "asr_end_time": 10.0},
        {
            "kind": "translation_partial",
            "playback_offset": 1.0,
            "asr_start_time": 0.0,
            "asr_end_time": 5.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 1.2,
            "asr_start_time": 0.0,
            "asr_end_time": 5.0,
        },
    ]
    batches = batch_latencies(events)
    assert len(batches) == 1
    b = batches[0]
    assert b.content_ready_wallclock is None
    assert b.content_ready_is_exact is None
    assert b.queue_wait_s is None
