"""Synthetic batch-list examples for caal_decomposition.py's pure functions
(h-caal-avoidable-unavoidable-al-decomposition, cycle 94)."""

import json

import pytest

from real_time_translation.experiments.caal_decomposition import (
    _classify_contiguous_batches,
    analyze_experiment,
    decompose_batches,
)


def _event(
    kind,
    playback_offset,
    asr_start_time,
    asr_end_time,
    is_utterance_end=True,
    text="",
):
    return {
        "kind": kind,
        "playback_offset": playback_offset,
        "asr_start_time": asr_start_time,
        "asr_end_time": asr_end_time,
        "is_utterance_end": is_utterance_end,
        "text": text,
    }


def test_decompose_batches_soft_finalized_duration_goes_to_avoidable():
    # One soft-finalized batch (is_utterance_end=False): queue_wait_s=2.0,
    # stream_duration_s=0.5, own duration=3.0 (asr 0.0 -> 3.0) -> all
    # attributed to avoidable_s since it's soft-finalized.
    events = [
        {"kind": "asr_interim", "playback_offset": 0.0, "asr_end_time": 3.0},
        _event("translation_partial", 2.0, 0.0, 3.0, is_utterance_end=False),
        _event("translation_complete", 2.5, 0.0, 3.0, is_utterance_end=False),
    ]
    decomps, excluded, ambiguous = decompose_batches(events)
    assert excluded == 0
    assert ambiguous == 0
    assert len(decomps) == 1
    d = decomps[0]
    assert d.is_utterance_end is False
    assert d.queue_wait_s == 2.0
    assert d.stream_duration_s == 0.5
    assert d.duration_s == 3.0
    assert d.avoidable_s == 2.0 + 3.0
    assert d.unavoidable_s == 0.5


def test_decompose_batches_genuine_final_duration_goes_to_unavoidable():
    events = [
        {"kind": "asr_interim", "playback_offset": 0.0, "asr_end_time": 4.0},
        _event("translation_partial", 1.0, 0.0, 4.0, is_utterance_end=True),
        _event("translation_complete", 1.5, 0.0, 4.0, is_utterance_end=True),
    ]
    decomps, excluded, ambiguous = decompose_batches(events)
    assert excluded == 0
    assert ambiguous == 0
    d = decomps[0]
    assert d.is_utterance_end is True
    assert d.avoidable_s == 1.0  # queue_wait_s only, no duration term
    assert d.unavoidable_s == 0.5 + 4.0  # stream_duration_s + duration


def test_decompose_batches_excludes_batch_with_no_queue_wait():
    # No asr_interim event reports asr_end_time=5.0, so content_ready is
    # unknown and queue_wait_s is None for this batch -> excluded entirely.
    events = [
        _event("translation_partial", 1.0, 0.0, 5.0, is_utterance_end=True),
        _event("translation_complete", 1.2, 0.0, 5.0, is_utterance_end=True),
    ]
    decomps, excluded, ambiguous = decompose_batches(events)
    assert excluded == 1
    assert ambiguous == 0
    assert decomps == []


def test_decompose_batches_ambiguous_utterance_end_omits_duration_term():
    events = [
        {"kind": "asr_interim", "playback_offset": 0.0, "asr_end_time": 2.0},
        _event("translation_partial", 1.0, 0.0, 2.0, is_utterance_end=True),
        _event("translation_complete", 1.3, 0.0, 2.0, is_utterance_end=False),
    ]
    decomps, excluded, ambiguous = decompose_batches(events)
    assert excluded == 0
    assert ambiguous == 1
    d = decomps[0]
    assert d.is_utterance_end is None
    assert d.avoidable_s == 1.0  # queue_wait_s only
    assert d.unavoidable_s == pytest.approx(0.3)  # stream_duration_s only


def test_classify_contiguous_batches_handles_non_contiguous_repeated_key():
    # The same (asr_start_time, asr_end_time) key appears twice,
    # non-contiguously, with DIFFERENT is_utterance_end flags each time --
    # this must produce two separate (key, flag) entries, not one merged
    # entry (see module docstring's join-key verification note).
    events = [
        _event("translation_partial", 1.0, 0.0, 1.0, is_utterance_end=False),
        _event("translation_partial", 2.0, 2.0, 2.5, is_utterance_end=True),
        _event("translation_partial", 3.0, 0.0, 1.0, is_utterance_end=True),
    ]
    classified = _classify_contiguous_batches(events)
    assert classified == [
        ((0.0, 1.0), False),
        ((2.0, 2.5), True),
        ((0.0, 1.0), True),
    ]


def test_analyze_experiment_computes_avoidable_fraction_and_ca_al(tmp_path):
    events = [
        {"kind": "asr_interim", "playback_offset": 0.0, "asr_end_time": 3.0},
        _event("translation_partial", 2.0, 0.0, 3.0, is_utterance_end=False),
        _event(
            "translation_complete",
            2.5,
            0.0,
            3.0,
            is_utterance_end=False,
            text="ABC",
        ),
        {"kind": "asr_interim", "playback_offset": 2.0, "asr_end_time": 6.0},
        _event(
            "translation_partial", 3.0, 3.0, 6.0, is_utterance_end=True, text="DE"
        ),
        _event(
            "translation_complete",
            4.0,
            3.0,
            6.0,
            is_utterance_end=True,
            text="DEF",
        ),
    ]
    data = {
        "experiment_name": "toy_caal",
        "domain": "toy",
        "input": {"type": "local_video", "path": "x.webm", "duration_seconds": 6.0},
        "results": {"events": events},
    }
    path = tmp_path / "toy.json"
    path.write_text(json.dumps(data))

    report = analyze_experiment(path)
    assert report is not None
    assert report.num_batches_total == 2
    assert report.num_batches_excluded_no_queue_wait == 0
    assert report.num_batches_ambiguous_utterance_end == 0
    # batch 1 (soft-finalized, asr 0.0-3.0): queue_wait=2.0, stream=0.5 ->
    #   avoidable = 2.0 + 3.0 = 5.0, unavoidable = 0.5
    # batch 2 (genuine-final, asr 3.0-6.0): queue_wait=1.0, stream=1.0 ->
    #   avoidable = 1.0, unavoidable = 1.0 + 3.0 = 4.0
    assert report.avoidable_s_total == pytest.approx(6.0)
    assert report.unavoidable_s_total == pytest.approx(4.5)
    assert report.avoidable_fraction == pytest.approx(6.0 / 10.5)
    assert report.ca_al_char_s is not None


def test_analyze_experiment_returns_none_for_no_events(tmp_path):
    data = {"experiment_name": "empty", "results": {"events": []}}
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(data))
    assert analyze_experiment(path) is None
