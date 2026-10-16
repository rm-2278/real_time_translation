"""Hand-computed toy examples for simul_latency_metrics.py's pure functions
(h-computation-aware-laal-enja, cycle 60)."""

import pytest

from real_time_translation.experiments.simul_latency_metrics import (
    _excluded_gaps,
    _gap_exclusion_offset,
    analyze_experiment,
    settle_times,
)


def test_settle_times_pure_append_is_first_appearance():
    timeline = [(1.0, "A"), (2.0, "AB"), (3.0, "ABC")]
    assert settle_times("ABC", timeline) == [1.0, 2.0, 3.0]


def test_settle_times_revision_delays_until_reconfirmed():
    # position 0 ('A') matches at every checkpoint -> settles immediately.
    # position 1 ('B') is clobbered to 'X' at t=2.0, only confirmed at t=3.0.
    # position 2 ('C') does not exist before t=3.0.
    timeline = [(1.0, "AB"), (2.0, "AX"), (3.0, "ABC")]
    assert settle_times("ABC", timeline) == [1.0, 3.0, 3.0]


def test_settle_times_empty_inputs():
    assert settle_times("", [(1.0, "")]) == []
    assert settle_times("ABC", []) == []


def test_al_toy_fixed_overhead_gives_constant_lag(tmp_path):
    # 10s source, 10 output chars, one emitted exactly i seconds after
    # playback start (i.e. the system is always exactly 1s "behind" a
    # zero-overhead ideal translator reading at the same rate as the
    # source) -> AL should come out to exactly 1.0s.
    text = "ABCDEFGHIJ"
    events = []
    for i in range(1, 11):
        events.append(
            {
                "kind": "translation_partial",
                "playback_offset": float(i),
                "text": text[:i],
                "asr_start_time": 0.0,
                "asr_end_time": 10.0,
                "is_utterance_end": i == 10,
            }
        )
    data = {
        "experiment_name": "toy",
        "domain": "toy",
        "input": {"type": "local_video", "path": "x.webm", "duration_seconds": 10.0},
        "results": {"events": events},
    }
    path = tmp_path / "toy.json"
    import json

    path.write_text(json.dumps(data))
    report = analyze_experiment(path)
    assert report is not None
    assert report.output_chars == 10
    assert report.ca_al_char_s == pytest.approx(1.0)


def test_excluded_gaps_only_flags_gaps_over_threshold():
    # gap1 = 6.0 - 5.0 = 1.0s (under the 10s threshold -> not excluded).
    # gap2 = 58.0 - 8.0 = 50.0s (over threshold -> excess 50.0 - 10.0 = 40.0s
    # excluded, starting once the gap itself has run for `threshold` seconds).
    intervals = [(0.0, 5.0), (6.0, 8.0), (58.0, 60.0)]
    gaps = _excluded_gaps(intervals, threshold_s=10.0)
    assert len(gaps) == 1
    g = gaps[0]
    assert g.gap_start == pytest.approx(8.0)
    assert g.excl_begin == pytest.approx(18.0)
    assert g.gap_end == pytest.approx(58.0)
    assert g.excluded_s == pytest.approx(40.0)


def test_gap_exclusion_offset_zero_before_partial_during_full_after():
    # gap = 58.0 - 5.0 = 53.0s -> excl_begin = 5.0 + 10.0 = 15.0,
    # excluded_s = 53.0 - 10.0 = 43.0.
    intervals = [(0.0, 5.0), (58.0, 60.0)]
    gaps = _excluded_gaps(intervals, threshold_s=10.0)
    assert _gap_exclusion_offset(gaps, 10.0) == pytest.approx(0.0)  # before excl_begin
    assert _gap_exclusion_offset(gaps, 30.0) == pytest.approx(15.0)  # mid-gap: 30-15
    assert _gap_exclusion_offset(gaps, 100.0) == pytest.approx(43.0)  # past the gap


def test_al_toy_mid_run_gap_exclusion_reduces_inflated_lag(tmp_path):
    # Two utterances separated by a 50s mid-run silence (asr_end=5.0 ->
    # asr_start=55.0), each emitting 2 chars append-only ("AB" then "CD").
    # Hand-computed without gap exclusion: t0=0, duration=60 (t_end=60),
    # gamma=4/60, settle=[1,2,56,57], ideal_delay=[0,15,30,45] ->
    # lags=[1,-13,26,12] -> ca_al_char_s = 26/4 = 6.5.
    # With the 10s-threshold gap exclusion, 40s of the 50s gap is excluded:
    # duration_gapexcl=20, gamma_gapexcl=4/20, offset(56)=offset(57)=40 (both
    # past the gap) -> settle_gapexcl=[1,2,16,17], ideal=[0,5,10,15] ->
    # lags=[1,-3,6,2] -> ca_al_char_s_gapexcl = 6/4 = 1.5.
    events = [
        {
            "kind": "translation_partial",
            "playback_offset": 1.0,
            "text": "A",
            "asr_start_time": 0.0,
            "asr_end_time": 5.0,
            "is_utterance_end": False,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 2.0,
            "text": "AB",
            "asr_start_time": 0.0,
            "asr_end_time": 5.0,
            "is_utterance_end": True,
        },
        {
            "kind": "translation_partial",
            "playback_offset": 56.0,
            "text": "C",
            "asr_start_time": 55.0,
            "asr_end_time": 60.0,
            "is_utterance_end": False,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 57.0,
            "text": "CD",
            "asr_start_time": 55.0,
            "asr_end_time": 60.0,
            "is_utterance_end": True,
        },
    ]
    data = {
        "experiment_name": "toy_gap",
        "domain": "toy",
        "input": {
            "type": "local_video",
            "path": "toy_gap.webm",
            "duration_seconds": 60.0,
        },
        "results": {"events": events},
    }
    path = tmp_path / "toy_gap.json"
    import json

    path.write_text(json.dumps(data))
    report = analyze_experiment(path)
    assert report is not None
    assert report.ca_al_char_s == pytest.approx(6.5)
    assert report.num_excluded_gaps == 1
    assert report.excluded_gap_total_s == pytest.approx(40.0)
    assert report.ca_al_char_s_gapexcl == pytest.approx(1.5)


def test_al_toy_no_large_gap_leaves_gapexcl_unchanged(tmp_path):
    # Reuses the fixed-overhead toy from above (single utterance, no gaps
    # at all) -- ca_al_char_s_gapexcl must equal ca_al_char_s exactly when
    # there is nothing to exclude.
    text = "ABCDEFGHIJ"
    events = []
    for i in range(1, 11):
        events.append(
            {
                "kind": "translation_partial",
                "playback_offset": float(i),
                "text": text[:i],
                "asr_start_time": 0.0,
                "asr_end_time": 10.0,
                "is_utterance_end": i == 10,
            }
        )
    data = {
        "experiment_name": "toy_nogap",
        "domain": "toy",
        "input": {"type": "local_video", "path": "x.webm", "duration_seconds": 10.0},
        "results": {"events": events},
    }
    path = tmp_path / "toy_nogap.json"
    import json

    path.write_text(json.dumps(data))
    report = analyze_experiment(path)
    assert report is not None
    assert report.num_excluded_gaps == 0
    assert report.ca_al_char_s_gapexcl == pytest.approx(report.ca_al_char_s)


def test_al_toy_ideal_rate_gives_zero_lag(tmp_path):
    # Same 10s/10-char setup, but each char is emitted exactly at its
    # "ideal" proportional time (char i at t=i-1) -> AL should be ~0.
    text = "ABCDEFGHIJ"
    events = []
    for i in range(1, 11):
        events.append(
            {
                "kind": "translation_partial",
                "playback_offset": float(i - 1),
                "text": text[:i],
                "asr_start_time": 0.0,
                "asr_end_time": 10.0,
                "is_utterance_end": i == 10,
            }
        )
    data = {
        "experiment_name": "toy2",
        "domain": "toy",
        "input": {"type": "local_video", "path": "x.webm", "duration_seconds": 10.0},
        "results": {"events": events},
    }
    path = tmp_path / "toy2.json"
    import json

    path.write_text(json.dumps(data))
    report = analyze_experiment(path)
    assert report is not None
    assert report.ca_al_char_s == pytest.approx(0.0, abs=1e-9)
