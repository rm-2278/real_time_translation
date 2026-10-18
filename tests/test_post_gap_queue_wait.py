"""Hand-computed toy examples for post_gap_queue_wait.py
(h-mt-queue-wait-post-gap-residual, cycle 62)."""

import json

import pytest

from real_time_translation.experiments.mt_latency_decomposition import (
    batch_latencies,
)
from real_time_translation.experiments.post_gap_queue_wait import (
    _tag_offset_batches,
    _tag_post_gap_batches,
    analyze_experiment,
    analyze_persistence,
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


def _toy_persistence_events():
    # Two excluded (>10s) gaps, back to back with only one baseline batch
    # (u1) before them: gap A (5.0 -> 50.0, 45s) anchors uA0, immediately
    # followed by gap B (55.0 -> 70.0, 15s) anchoring uB0 one batch later --
    # this tests that uA0's offset run is truncated to just offset 0 and
    # does not swallow uB0 as its own "offset 1". Gap B's anchor uB0 is
    # then followed by two sub-threshold-gap batches (uB1, uB2) with
    # decaying queue_wait_s (7.0 -> 4.0 -> 1.1), then a final batch
    # (u_final) whose queue_wait_s (0.9) drops back at/below the lone
    # baseline sample (u1's 1.0) -- testing that the elevated-offset
    # accumulation stops there rather than continuing indefinitely.
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
            "text": "u1",
            "asr_start_time": 0.0,
            "asr_end_time": 5.0,
            "is_utterance_end": True,
        },
        # uA0: anchor for gap A (gap_end == 50.0). queue_wait_s = 8.0.
        {
            "kind": "asr_interim",
            "playback_offset": 50.2,
            "asr_start_time": 50.0,
            "asr_end_time": 55.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 58.2,
            "text": "uA0",
            "asr_start_time": 50.0,
            "asr_end_time": 55.0,
            "is_utterance_end": True,
        },
        # uB0: anchor for gap B (gap_end == 70.0, 15s after uA0 ends at
        # 55.0). queue_wait_s = 7.0.
        {
            "kind": "asr_interim",
            "playback_offset": 70.1,
            "asr_start_time": 70.0,
            "asr_end_time": 75.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 77.1,
            "text": "uB0",
            "asr_start_time": 70.0,
            "asr_end_time": 75.0,
            "is_utterance_end": True,
        },
        # uB1: 1s sub-threshold gap after uB0. queue_wait_s = 4.0 (still
        # elevated, decaying).
        {
            "kind": "asr_interim",
            "playback_offset": 76.1,
            "asr_start_time": 76.0,
            "asr_end_time": 80.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 80.1,
            "text": "uB1",
            "asr_start_time": 76.0,
            "asr_end_time": 80.0,
            "is_utterance_end": True,
        },
        # uB2: 1s sub-threshold gap after uB1. queue_wait_s = 1.1 (barely
        # above the 1.0 baseline).
        {
            "kind": "asr_interim",
            "playback_offset": 81.1,
            "asr_start_time": 81.0,
            "asr_end_time": 85.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 82.2,
            "text": "uB2",
            "asr_start_time": 81.0,
            "asr_end_time": 85.0,
            "is_utterance_end": True,
        },
        # u_final: 5s sub-threshold gap after uB2. queue_wait_s = 0.9, at
        # or below the lone baseline sample -- decay has finished.
        {
            "kind": "asr_interim",
            "playback_offset": 90.1,
            "asr_start_time": 90.0,
            "asr_end_time": 95.0,
        },
        {
            "kind": "translation_complete",
            "playback_offset": 91.0,
            "text": "u_final",
            "asr_start_time": 90.0,
            "asr_end_time": 95.0,
            "is_utterance_end": True,
        },
    ]


def test_tag_offset_batches_truncates_before_next_gaps_anchor():
    events = _toy_persistence_events()
    intervals = _asr_utterance_intervals(events)
    excluded = _excluded_gaps(intervals, threshold_s=10.0)
    assert len(excluded) == 2

    batches = batch_latencies(events)
    tagged = _tag_offset_batches(batches, excluded)

    # uA0 (50.0, 55.0) gets ONLY offset 0 -- its would-be offset 1 is
    # uB0, which is gap B's own offset-0 anchor, not gap A's offset 1.
    assert tagged[(50.0, 55.0)] == 0
    assert tagged[(70.0, 75.0)] == 0
    assert tagged[(76.0, 80.0)] == 1
    assert tagged[(81.0, 85.0)] == 2
    assert tagged[(90.0, 95.0)] == 3
    # u1 (0.0, 5.0) has no preceding gap at all -- never tagged.
    assert (0.0, 5.0) not in tagged


def test_analyze_persistence_decays_and_stops_at_baseline(tmp_path):
    events = _toy_persistence_events()
    data = {
        "experiment_name": "toy_persistence",
        "domain": "toy",
        "input": {
            "type": "local_video",
            "path": "toy_persistence.webm",
            "duration_seconds": 95.0,
        },
        "results": {"events": events},
    }
    path = tmp_path / "toy_persistence.json"
    path.write_text(json.dumps(data))

    report = analyze_persistence(path)
    assert report is not None
    assert report.num_excluded_gaps == 2

    # Only u1's queue_wait_s (1.0) is untagged baseline.
    assert report.baseline_queue_wait_mean_s == pytest.approx(1.0)

    # offset 0 pools uA0 (8.0) and uB0 (7.0) -> mean 7.5, n=2.
    assert report.per_offset_queue_wait_mean_s[0] == pytest.approx(7.5)
    assert report.per_offset_n[0] == 2
    assert report.per_offset_queue_wait_mean_s[1] == pytest.approx(4.0)
    assert report.per_offset_queue_wait_mean_s[2] == pytest.approx(1.1)
    assert report.per_offset_queue_wait_mean_s[3] == pytest.approx(0.9)

    # offset 3 (0.9) is <= baseline (1.0), so accumulation stops at
    # offset 2 even though offset 3 was tagged and reported.
    assert report.max_elevated_offset == 2
    expected_excess = (8.0 - 1.0) + (7.0 - 1.0) + (4.0 - 1.0) + (1.1 - 1.0)
    assert report.cumulative_excess_queue_wait_s == pytest.approx(expected_excess)
    assert report.ca_al_char_s_gapexcl is not None
    expected_share = expected_excess / report.ca_al_char_s_gapexcl
    assert report.cumulative_explanatory_share == pytest.approx(expected_share)


def test_analyze_persistence_none_when_no_excluded_gaps(tmp_path):
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
    ]
    data = {
        "experiment_name": "toy_no_gap_persistence",
        "domain": "toy",
        "input": {
            "type": "local_video",
            "path": "toy_no_gap_persistence.webm",
            "duration_seconds": 4.0,
        },
        "results": {"events": events},
    }
    path = tmp_path / "toy_no_gap_persistence.json"
    path.write_text(json.dumps(data))

    assert analyze_persistence(path) is None
