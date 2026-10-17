"""Hand-computed toy examples for post_gap_queue_wait.py
(h-mt-queue-wait-post-gap-residual, cycle 62)."""

import json

import pytest

from real_time_translation.experiments.mt_latency_decomposition import (
    batch_latencies,
)
from real_time_translation.experiments.post_gap_queue_wait import (
    _tag_post_gap_batches,
    analyze_experiment,
)
from real_time_translation.experiments.simul_latency_metrics import (
    _asr_utterance_intervals,
    _excluded_gaps,
)


def _toy_events():
    # Four utterances. Utterance 2 (asr 55.0-60.0) starts right after a 50s
    # gap following utterance 1 (asr 0.0-5.0) -- well over the 10s
    # exclusion threshold, so this is the one gap in this toy run.
    # Utterance 3 (asr 61.0-65.0) follows utterance 2 with only a 1s gap
    # (under threshold) -- not excluded, so utterance 3's batch must stay
    # in the baseline group even though it comes right after utterance 2.
    # Utterance 4 (asr 70.0-75.0) follows with a 5s gap -- also under
    # threshold, also baseline.
    #
    # asr_interim events establish each utterance's "content ready" time
    # (mt_latency_decomposition._content_ready_lookup keys off asr_interim
    # events), and translation_complete events are each batch's only
    # translation event (queue_wait_s = translation playback_offset minus
    # content_ready playback_offset).
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
            "text": "A",
            "asr_start_time": 0.0,
            "asr_end_time": 5.0,
            "is_utterance_end": True,
        },
        # utterance 2: starts at 55.0, right after the 50s gap. A large
        # queue_wait_s (8.0s) simulates the "post-gap backlog" effect this
        # hypothesis is testing for.
        {
            "kind": "asr_interim",
            "playback_offset": 55.2,
            "asr_start_time": 55.0,
            "asr_end_time": 60.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 63.2,
            "text": "B",
            "asr_start_time": 55.0,
            "asr_end_time": 60.0,
            "is_utterance_end": True,
        },
        # utterance 3: starts at 61.0 -- only 1s after utterance 2 ends
        # (60.0), under the 10s threshold, so NOT post-gap. Small,
        # baseline-sized queue_wait_s (1.0s).
        {
            "kind": "asr_interim",
            "playback_offset": 61.1,
            "asr_start_time": 61.0,
            "asr_end_time": 65.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 62.1,
            "text": "C",
            "asr_start_time": 61.0,
            "asr_end_time": 65.0,
            "is_utterance_end": True,
        },
        # utterance 4: starts at 70.0 -- 5s after utterance 3 ends (65.0),
        # also under threshold. Another baseline-sized queue_wait_s (1.2s).
        {
            "kind": "asr_interim",
            "playback_offset": 70.1,
            "asr_start_time": 70.0,
            "asr_end_time": 75.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 71.3,
            "text": "D",
            "asr_start_time": 70.0,
            "asr_end_time": 75.0,
            "is_utterance_end": True,
        },
    ]


def test_tag_post_gap_batches_matches_closest_batch_within_tolerance():
    events = _toy_events()
    intervals = _asr_utterance_intervals(events)
    excluded = _excluded_gaps(intervals, threshold_s=10.0)
    assert len(excluded) == 1
    assert excluded[0].gap_end == pytest.approx(55.0)

    batches = batch_latencies(events)
    tagged = _tag_post_gap_batches(batches, excluded)

    # Only the (55.0, 60.0) batch (utterance 2) is tagged -- its
    # asr_start_time (55.0) exactly matches the gap's gap_end (55.0).
    assert set(tagged.keys()) == {(55.0, 60.0)}
    # Utterances 1, 3, 4 are untagged (utterance 1 has no preceding gap;
    # utterances 3/4 follow only sub-threshold gaps).
    untagged_keys = {b.key for b in batches} - set(tagged.keys())
    assert untagged_keys == {(0.0, 5.0), (61.0, 65.0), (70.0, 75.0)}


def test_analyze_experiment_separates_post_gap_from_baseline_queue_wait(tmp_path):
    events = _toy_events()
    data = {
        "experiment_name": "toy_post_gap",
        "domain": "toy",
        "input": {
            "type": "local_video",
            "path": "toy_post_gap.webm",
            "duration_seconds": 75.0,
        },
        "results": {"events": events},
    }
    path = tmp_path / "toy_post_gap.json"
    path.write_text(json.dumps(data))

    report = analyze_experiment(path)
    assert report is not None
    assert report.num_excluded_gaps == 1
    assert report.num_post_gap_resolved == 1

    # queue_wait_s per batch: utterance1 1.5-0.5=1.0 (baseline, no
    # preceding gap at all so it's just baseline-by-default); utterance2
    # 63.2-55.2=8.0 (post-gap); utterance3 62.1-61.1=1.0 (baseline);
    # utterance4 71.3-70.1=1.2 (baseline).
    assert report.post_gap_queue_wait_mean_s == pytest.approx(8.0)
    expected_baseline_mean = (1.0 + 1.0 + 1.2) / 3
    assert report.baseline_queue_wait_mean_s == pytest.approx(expected_baseline_mean)
    expected_overall_mean = (1.0 + 8.0 + 1.0 + 1.2) / 4
    assert report.overall_queue_wait_mean_s == pytest.approx(expected_overall_mean)

    # excess_queue_wait_total_s = post_gap_wait - baseline_mean
    expected_excess = 8.0 - expected_baseline_mean
    assert report.excess_queue_wait_total_s == pytest.approx(expected_excess)
    assert report.ca_al_char_s_gapexcl is not None


def test_no_excluded_gaps_gives_no_post_gap_data(tmp_path):
    # Reuses only the sub-threshold-gap utterances (renumbered) -- no gap
    # exceeds the 10s threshold, so there is nothing to tag and the report
    # should reflect that cleanly.
    events = [
        {
            "kind": "asr_interim",
            "playback_offset": 0.1,
            "asr_start_time": 0.0,
            "asr_end_time": 4.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 1.1,
            "text": "C",
            "asr_start_time": 0.0,
            "asr_end_time": 4.0,
            "is_utterance_end": True,
        },
        {
            "kind": "asr_interim",
            "playback_offset": 9.1,
            "asr_start_time": 9.0,
            "asr_end_time": 14.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 10.3,
            "text": "D",
            "asr_start_time": 9.0,
            "asr_end_time": 14.0,
            "is_utterance_end": True,
        },
    ]
    data = {
        "experiment_name": "toy_no_gap",
        "domain": "toy",
        "input": {
            "type": "local_video",
            "path": "toy_no_gap.webm",
            "duration_seconds": 14.0,
        },
        "results": {"events": events},
    }
    path = tmp_path / "toy_no_gap.json"
    path.write_text(json.dumps(data))

    report = analyze_experiment(path)
    assert report is not None
    assert report.num_excluded_gaps == 0
    assert report.num_post_gap_resolved == 0
    assert report.post_gap_queue_wait_mean_s is None
    assert report.excess_queue_wait_total_s is None
    assert report.explanatory_share_of_gapexcl_al is None
