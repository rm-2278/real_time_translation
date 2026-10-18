"""Hand-picked examples for word_order_reordering_proxy.py
(h-enja-word-order-reordering-proxy-latency, cycle 64)."""

import json

import pytest

from real_time_translation.experiments.word_order_reordering_proxy import (
    _spearman_r,
    _verb_position_ratio,
    collect_experiment,
    summarize,
)


def test_verb_position_ratio_finds_early_auxiliary():
    # "is" is token index 1 of 4 tokens ("this", "is", "a", "test").
    assert _verb_position_ratio("This is a test") == pytest.approx(1 / 4)


def test_verb_position_ratio_finds_late_modal():
    # "because of the very long delay before the committee could finally
    # decide" -- "could" is token index 9 of 12 tokens.
    sentence = (
        "because of the very long delay before the committee could finally decide"
    )
    assert _verb_position_ratio(sentence) == pytest.approx(9 / 12)


def test_verb_position_ratio_unresolved_without_closed_class_token():
    # Simple present, no auxiliary/modal/copula token at all.
    assert _verb_position_ratio("I like cats") is None


def test_verb_position_ratio_empty_sentence_is_none():
    assert _verb_position_ratio("") is None
    assert _verb_position_ratio("   ") is None


def _toy_events():
    # Two is_utterance_end=True utterances with resolvable verb positions
    # and queue_wait_s (content-ready asr_interim + a single
    # translation_complete per utterance, matching mt_latency_decomposition's
    # expected event shape).
    return [
        {
            "kind": "asr_interim",
            "playback_offset": 0.5,
            "asr_start_time": 0.0,
            "asr_end_time": 5.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 1.5,
            "text": "これはテストです",
            "original_text": "This is a test",
            "asr_start_time": 0.0,
            "asr_end_time": 5.0,
            "is_utterance_end": True,
        },
        {
            "kind": "asr_interim",
            "playback_offset": 10.0,
            "asr_start_time": 10.0,
            "asr_end_time": 15.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 13.0,
            "text": "緳",
            "original_text": "I like cats",
            "asr_start_time": 10.0,
            "asr_end_time": 15.0,
            "is_utterance_end": True,
        },
        # A mid-utterance partial (is_utterance_end=False) must be ignored
        # even though it is also kind="translation_complete".
        {
            "kind": "translation_complete",
            "playback_offset": 20.0,
            "text": "partial",
            "original_text": "partial source",
            "asr_start_time": 20.0,
            "asr_end_time": 25.0,
            "is_utterance_end": False,
        },
    ]


def test_collect_experiment_skips_non_utterance_end_and_resolves_latency(tmp_path):
    data = {
        "experiment_name": "toy_word_order",
        "results": {"events": _toy_events()},
    }
    path = tmp_path / "toy_word_order.json"
    path.write_text(json.dumps(data))

    points = collect_experiment(path)
    assert len(points) == 2

    first, second = points
    assert first.key == (0.0, 5.0)
    assert first.verb_position_ratio == pytest.approx(1 / 4)
    assert first.queue_wait_s == pytest.approx(1.0)

    assert second.key == (10.0, 15.0)
    assert second.verb_position_ratio is None
    assert second.queue_wait_s == pytest.approx(3.0)


def test_summarize_reports_resolution_rate_and_correlation(tmp_path):
    data = {
        "experiment_name": "toy_word_order",
        "results": {"events": _toy_events()},
    }
    path = tmp_path / "toy_word_order.json"
    path.write_text(json.dumps(data))

    points = collect_experiment(path)
    summary = summarize(points)
    assert summary.num_utterances == 2
    assert summary.num_resolved_proxy == 1
    assert summary.resolution_rate == pytest.approx(0.5)
    # Only one utterance has a resolved proxy, so no 2-point correlation.
    assert summary.queue_wait_pearson_r is None


def test_spearman_r_perfect_monotonic_relationship():
    xs = [0.1, 0.2, 0.3, 0.4]
    ys = [10.0, 20.0, 30.0, 40.0]
    assert _spearman_r(xs, ys) == pytest.approx(1.0)


def test_spearman_r_handles_ties():
    xs = [0.1, 0.1, 0.3, 0.4]
    ys = [5.0, 7.0, 7.0, 20.0]
    r = _spearman_r(xs, ys)
    assert r is not None
    assert -1.0 <= r <= 1.0
