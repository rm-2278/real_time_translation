"""Hand-computed toy examples for simul_latency_metrics.py's pure functions
(h-computation-aware-laal-enja, cycle 60)."""

import pytest

from real_time_translation.experiments.simul_latency_metrics import (
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
