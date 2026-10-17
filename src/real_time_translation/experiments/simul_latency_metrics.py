"""Character-level, computation-aware Average Lagging (AL) for this
pipeline's existing experiment runs, retroactively, with no new ASR/LLM
calls -- an external yardstick next to SimulEval-style SimulST literature.

Motivation (h-computation-aware-laal-enja, cycle 60 GENERATE_HYPOTHESES):
`quality_latency_joint.py` deliberately skips AL/LAAL/YAAL, reasoning that
"token-level source-consumption formulas need per-token source-alignment
data this pipeline's event log does not carry." That is true for TEXT
source (STACL/wait-k's token-count formulation), but this pipeline's
source is SPEECH: `ma2019-stacl-waitk`'s AL, adapted to speech input (as
`simulseamless2024-fbk-iwslt` does, reporting IWSLT 2024 En->Ja AL at
*character* level for Japanese), needs only an emission timestamp on the
source-audio clock -- no token alignment at all. Every experiment JSON's
`results.events` already carries a `playback_offset` (wall-clock seconds
since the run started) on every event, including
`translation_partial`/`translation_complete`, and that wall-clock time
already includes Deepgram + queueing + LLM wall time, i.e. it is already
*computation-aware* by construction (this pipeline has no "compute-free"
mode to compare against).

Checked before implementing, per PLAYBOOK.md's standing instruction to
verify `playback_offset`'s exact meaning against the construction site
rather than assume it (`video_segment.py`'s `on_result()`): `playback_offset`
is captured once per `_on_result` callback invocation, and
`pipeline.py`'s `_stream_batch()` calls `_on_result` once per *streamed LLM
chunk* while `live=True` (the normal, non-continuation case) -- so
`translation_partial` events really do carry one timestamp per streamed
text delta, not one coarse "batch received" timestamp. `translation_complete`
(emitted by `_finish_batch`/`_emit_batch_result`) is the exception: it is
timestamped at the moment the batch is *released from the in-order emit
buffer*, which can lag the moment its own LLM call actually finished if an
earlier batch was still in flight -- i.e. for `translation_complete`
specifically, `playback_offset` is "batch delivered to the viewer in order",
not "last token streamed". This matters for the per-character timeline
below: it is built from *every* `translation_partial`/`translation_complete`
event's own `playback_offset`, so a `translation_complete`-only batch (the
`live=False` continuation-retranslation case, which emits no partials at
all per `_stream_batch`'s own docstring) is timestamped coarsely at
delivery time, while a normal `live=True` batch gets the finer-grained
per-chunk timestamps. Both are handled by the same settle-time algorithm
below; the continuation case is just a timeline with fewer checkpoints.

Algorithm
---------
For one experiment run, treat the *whole run* as a single long document
(deliberately not resetting at each ASR utterance boundary -- the
"StreamLAAL" style long-form framing `papi2025-howreal` and
`polak2026-meta-evaluation-latency-metrics` both argue for, instead of the
classic per-sentence AL that only make sense for pre-segmented corpora):

1. Group `translation_partial`/`translation_complete` events into
   utterance spans using the same `(asr_start_time, asr_end_time)` batch
   key, chained across continuation batches by `is_utterance_end`, as
   `flicker_metrics.group_translation_by_utterance()` -- see that
   function's own docstring for the "no real `utterance_id` field, this is
   a sequential heuristic" caveat, which applies here identically.
2. Within a span, a later batch's text is NOT guaranteed to share a prefix
   with an earlier batch's (continuation batches retranslate from scratch;
   see `pipeline.py`'s `_stream_batch` docstring) -- so a character
   position's true "emission time" is the *first* timeline checkpoint
   after which it never again mismatches the span's own final text. See
   `settle_times()` below (and its module-level examples) for the exact
   (and, on a revision, non-obvious) definition and a worked trace.
3. Concatenate every span's final text, in chronological span order, into
   one long per-experiment target character stream; concatenate each
   span's settle times the same way. `playback_offset` is monotonically
   non-decreasing across the whole event list (events are appended in the
   single-threaded callback's call order, which is itself already causal
   thanks to `pipeline.py`'s in-order emit buffer), so this concatenation
   needs no cross-span time adjustment.
4. Compute character-level AL exactly as `ma2019-stacl-waitk` defines it,
   substituting elapsed source-audio seconds for "source tokens read", and
   anchored to `t0` (the first utterance's own `asr_start_time`, not the
   clip's nominal t=0 -- see "Leading/trailing silence" below) and
   `S = t_end - t0` (the speech-content span):
   `gamma = N / S` (output chars / `S`), `ideal_delay(i) = (i-1)/gamma`,
   `d_i = settle_times()[i] - t0`, `tau` = the first `i` whose
   `d_i >= S` (source fully available; later output is just finishing up,
   not "lagging" in the online sense -- this cutoff is what stops a
   system from gaming AL by padding with trailing tokens after it has
   already caught up, per `ma2019-stacl-waitk`), `AL = mean(d_i -
   ideal_delay(i) for i in 1..tau)`.

`ca_al_vtt_refrate_char_s` caveat -- deliberately **not** called "LAAL":
this is the same `d_i`/`tau` as `ca_al_char_s` above, but with `gamma`
computed from a *reference* Japanese character count (summed from
`experiments/refs/<video_id>.ja.vtt`'s cues inside this run's own
[start, start+duration) window) instead of this run's own output length --
i.e. "what would AL be if the ideal pacing line were set by the
reference caption's length instead of this run's own output length."
This is motivated by the same complaint `Papi et al. 2022`'s LAAL raises
(don't let a system's own, possibly inflated or truncated, output length
set the yardstick it's graded against), but it is **not** a
reimplementation of that paper's actual formula (which redefines `tau`
itself around a reference-derived rate, not re-derived here), and a
sanity check while writing this module found its sign/magnitude
*counter-intuitive*: this pipeline's JA output is longer (more verbose)
than the reference caption for the same window, and the ref-rate
correction comes out *more negative* (farther from "no lag") than the
self-rate `ca_al_char_s`, i.e. an over-generating run looks even faster
once "corrected" -- the opposite of what a LAAL-style over-generation
penalty should do. That strongly suggests this column's correction
direction is wrong relative to Papi et al.'s actual intent, not merely
imprecise -- a future READ_PAPERS pass should check the primary source
before this column is trusted for anything beyond "does using a
different output-length assumption change the number" (it does, a lot).
Only available for runs whose `input.path` stem has a matching `.ja.vtt`
under `experiments/refs/` (currently only the `wjZofJX0v4M` clip, ~72 of
this repo's experiment JSONs).

Leading/trailing silence: the ideal-rate line is anchored to
`[first_asr_start_time, last_asr_end_time]` (the speech-content span),
not `[0, declared_clip_duration]` -- found empirically necessary while
validating this module (see `reflections.md` cycle 60): the full-lecture
experiments have ~75s of dead air before the first utterance, and
anchoring the ideal line at t=0 made every character look ~80-430s
"behind" purely from that lead-in, swamping the real signal (confirmed by
checking `ca_al_char_s` at increasing character indices on
`20260918_llm2025_ep8_zenhan_full_lecture_v2_dict.json`: lag *shrinks*
monotonically from the start as the constant-rate assumption "catches
up", the opposite of what a genuinely accumulating backlog would look
like). This fix only removes *lead-in/trail-off* dead air, not mid-run
pauses between utterances -- those remain unmodeled, and are exactly the
segmentation-sensitivity `polak2026-meta-evaluation-latency-metrics`
(SOFTSEGMENTER) was written to address for long-form audio. `ca_al_char_s`
is therefore a rough positioning number against SimulSeamless's reported
En->Ja operating point (BLEU 22.19 @ AL 1.997s, LAAL 2.137s,
**computation-aware AL 4.018s** -- the comparable figure, since every
number in this module already includes full Deepgram+queue+LLM wall
time), not a claim of metric parity -- see the Japanese report and
`reflections.md` for how large the residual mid-run-pause effect still
is on this repo's own long-form runs.

Mid-run gap exclusion (h-ca-al-gap-exclusion-softsegmenter, cycle 61):
`ca_al_char_s_gapexcl` extends the fix above to *mid-run* silence, the gap
left open by the note above. Confirmed empirically across all 102
experiment JSONs (deduped, sorted `(asr_start_time, asr_end_time)`
utterance intervals from every event, not just translation events): every
run other than the two `20260918_llm2025_ep8_zenhan_full_lecture*` ones has
a largest inter-utterance gap of at most 8.25s (`20260722_
en_talk_transition_accuracy_check.json`), while those two lecture runs have
8 and 7 gaps respectively (not "6 gaps each" as an earlier draft of this
note claimed -- corrected cycle 62 after re-running `_excluded_gaps()` and
counting programmatically: 8 gaps/239.17s excluded for
`llm2025_ep8_zenhan_full_lecture`, 7 gaps/251.17s for `_v2_dict`, matching
the cycle 61 Japanese report, not this docstring's prior "6" claim) of
12.0-103.76s (totalling ~235-251s of extended dead air per run) with
nothing in between the two populations (no gap anywhere in the corpus
falls in (8.25s, 12.0s]). `GAP_EXCLUSION_THRESHOLD_S = 10.0`
(this module's constant) sits in that empty zone by construction, so it
reclassifies exactly the six outlier-lecture gaps as "extended" and leaves
every other run's gap structure, and therefore its `ca_al_char_s_gapexcl`
value, identical to `ca_al_char_s` (see `_exclude_large_gaps()`). This is
**not** a reimplementation of SOFTSEGMENTER's actual (cross-system,
reference-aware) algorithm -- it is a simplified, single-system,
single-condition approximation of its spirit (don't let an
arbitrary-length silence masquerade as "the system is behind"): for each
gap longer than the threshold, only the *excess* beyond the threshold is
excluded from both the elapsed-time denominator `S` and every subsequent
settle time (i.e. the ideal-rate clock is treated as paused only for the
excess portion, not the whole gap -- a gap at or under the threshold is
still charged normally, consistent with every well-behaved run never
exceeding 8.25s). See the Japanese report and `reflections.md` cycle 61
for whether this moves the two outlier runs' AL into the same range as
the rest of the corpus, or leaves a residual gap worth treating as a real
pipeline effect rather than a pure measurement artifact.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from dataclasses import dataclass
from pathlib import Path

from real_time_translation.experiments.stats import mean, median

_VTT_TIMESTAMP_RE = re.compile(r"(\d\d:\d\d:\d\d\.\d\d\d) --> (\d\d:\d\d:\d\d\.\d\d\d)")
_VTT_TAG_RE = re.compile(r"<[^>]+>")

# See the module docstring's "Mid-run gap exclusion" section for the
# empirical derivation: every experiment other than the two outlier full
# lecture runs has a largest inter-utterance gap of at most 8.25s, while
# those two have several gaps of 12.0-103.76s -- this threshold sits in the
# empty zone between the two populations.
GAP_EXCLUSION_THRESHOLD_S = 10.0


def _longest_common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def settle_times(final_text: str, timeline: list[tuple[float, str]]) -> list[float]:
    """For each character position in `final_text` (1..len), the earliest
    timestamp in `timeline` (chronological `(playback_offset, text)` pairs
    for the same span, ending with `(_, final_text)`) after which that
    position never again mismatches `final_text`.

    Pure append-only growth (every `timeline` text is a prefix of the
    next) reduces to "the first time this position appeared", as expected.
    A revision (a later text that diverges from an earlier one, e.g. this
    pipeline's continuation-retranslation batches) delays the settle time
    for every position invalidated by that revision until it is confirmed
    again -- worked example: `timeline=[(1.0,"AB"),(2.0,"AX"),(3.0,"ABC")]`,
    `final_text="ABC"` gives `[1.0, 3.0, 3.0]`: position 0 ('A') matched at
    every checkpoint so it settles at the first one; position 1 ('B') was
    clobbered to 'X' at t=2.0 and only settles once the final batch
    confirms it at t=3.0; position 2 ('C') did not exist before t=3.0.
    """
    n = len(final_text)
    if n == 0 or not timeline:
        return []
    lcp = [_longest_common_prefix_len(text, final_text) for _, text in timeline]
    m = len(lcp)
    suffix_min = [0] * m
    running = n
    for j in range(m - 1, -1, -1):
        running = min(running, lcp[j])
        suffix_min[j] = running
    settle: list[float] = []
    j = 0
    for i in range(1, n + 1):
        while j < m and suffix_min[j] < i:
            j += 1
        settle.append(timeline[j][0] if j < m else timeline[-1][0])
    return settle


def _translation_spans(events: list[dict]) -> list[list[tuple[float, str]]]:
    """Chronological `(playback_offset, text)` pairs per utterance span --
    same batch/span grouping as `flicker_metrics.group_translation_by_utterance()`
    (see its docstring for the `is_utterance_end`-heuristic caveat), but
    keeping every individual event rather than collapsing to each batch's
    last text, so within-batch streaming growth contributes its own settle
    checkpoints."""
    spans: list[list[tuple[float, str]]] = []
    current: list[tuple[float, str]] = []
    prev_ended = True
    for e in events:
        if e.get("kind") not in ("translation_partial", "translation_complete"):
            continue
        if prev_ended and current:
            spans.append(current)
            current = []
        current.append((e.get("playback_offset", 0.0), e.get("text", "")))
        prev_ended = e.get("is_utterance_end", True)
    if current:
        spans.append(current)
    return spans


def _speech_span(events: list[dict]) -> tuple[float, float] | None:
    """`(t0, t_end)`: the first `asr_start_time` and last `asr_end_time`
    across all events, i.e. the speech-content span with lead-in/trail-off
    dead air excluded (see the module docstring's "Leading/trailing
    silence" note for why this matters)."""
    starts = [
        e.get("asr_start_time") for e in events if e.get("asr_start_time") is not None
    ]
    ends = [
        e.get("asr_end_time") for e in events if e.get("asr_end_time") is not None
    ]
    if not starts or not ends:
        return None
    return min(starts), max(ends)


def _asr_utterance_intervals(events: list[dict]) -> list[tuple[float, float]]:
    """Deduped, chronologically sorted `(asr_start_time, asr_end_time)`
    pairs across *every* event (any `kind`), not grouped into translation
    spans -- grouping by translation-span chaining was tried first and
    silently merged several of these intervals across the very gaps this
    function needs to find (continuation/revision batches can share a span
    across what is, at the raw-utterance level, a large real gap), so this
    works directly off the raw per-event timestamps instead, validated
    against the exact gap sizes quoted in the module docstring."""
    seen: set[tuple[float, float]] = set()
    intervals: list[tuple[float, float]] = []
    for e in events:
        start, end = e.get("asr_start_time"), e.get("asr_end_time")
        if start is None or end is None:
            continue
        key = (round(start, 3), round(end, 3))
        if key not in seen:
            seen.add(key)
            intervals.append(key)
    intervals.sort()
    return intervals


@dataclass(frozen=True)
class _ExcludedGap:
    gap_start: float  # end of the utterance before the gap
    excl_begin: float  # gap_start + threshold -- exclusion starts here
    gap_end: float  # start of the utterance after the gap
    excluded_s: float  # gap_end - excl_begin


def _excluded_gaps(
    intervals: list[tuple[float, float]], threshold_s: float
) -> list[_ExcludedGap]:
    gaps: list[_ExcludedGap] = []
    for (_, prev_end), (next_start, _) in zip(intervals, intervals[1:], strict=False):
        gap = next_start - prev_end
        if gap > threshold_s:
            gaps.append(
                _ExcludedGap(
                    gap_start=prev_end,
                    excl_begin=prev_end + threshold_s,
                    gap_end=next_start,
                    excluded_s=gap - threshold_s,
                )
            )
    return gaps


def _gap_exclusion_offset(excluded: list[_ExcludedGap], t: float) -> float:
    """Cumulative excluded time charged against elapsed-time `t`: 0 before
    a gap's exclusion begins, the excess within it while `t` is inside the
    excluded portion, the gap's full excluded amount once `t` is past it."""
    total = 0.0
    for g in excluded:
        if t <= g.excl_begin:
            continue
        total += min(t, g.gap_end) - g.excl_begin
    return total


def _source_duration_seconds(input_cfg: dict, events: list[dict]) -> float | None:
    span = _speech_span(events)
    if span is not None:
        t0, t_end = span
        if t_end > t0:
            return t_end - t0
    dur = input_cfg.get("duration_seconds")
    if isinstance(dur, (int, float)) and dur and dur > 0:
        return float(dur)
    start, end = input_cfg.get("start_seconds"), input_cfg.get("end_seconds")
    if (
        isinstance(start, (int, float))
        and isinstance(end, (int, float))
        and end > start
    ):
        return float(end - start)
    return None


def reference_char_count(vtt_path: Path, start: float, end: float) -> int:
    """Summed length of de-duplicated cue text in `vtt_path` whose cue
    starts within `[start, end)` seconds -- same cue-selection/dedup logic
    as `experiments/refs/extract_vtt_range.py` (reimplemented here since
    that script lives outside `src/`, not an importable package module),
    used only to get a rough *reference output length* for
    `ca_al_vtt_refrate_char_s`."""
    raw = vtt_path.read_text(encoding="utf-8")
    seen: list[str] = []
    last = None
    for block in raw.split("\n\n"):
        m = _VTT_TIMESTAMP_RE.search(block)
        if not m:
            continue
        bstart = _vtt_to_seconds(m.group(1))
        if not (start <= bstart < end):
            continue
        lines = block.split("\n")
        text_lines = [
            _VTT_TAG_RE.sub("", ln).strip()
            for ln in lines[lines.index(m.group(0)) + 1 :]
            if ln.strip()
        ]
        text = " ".join(text_lines).strip()
        if text and text != last:
            seen.append(text)
            last = text
    return sum(len(t) for t in seen)


def _vtt_to_seconds(ts: str) -> float:
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _vtt_reference(input_cfg: dict, duration: float) -> tuple[str, int] | None:
    path = input_cfg.get("path") or ""
    if not path:
        return None
    video_id = Path(path.replace("\\", "/")).stem
    vtt_path = Path("experiments/refs") / f"{video_id}.ja.vtt"
    if not vtt_path.exists():
        return None
    start = input_cfg.get("start_seconds") or 0.0
    ref_chars = reference_char_count(vtt_path, float(start), float(start) + duration)
    return (video_id, ref_chars) if ref_chars > 0 else None


@dataclass(frozen=True)
class AlReport:
    json_path: str
    experiment_name: str
    domain: str
    source_duration_s: float
    output_chars: int
    num_spans: int
    gamma_chars_per_s: float
    tau_char_index: int
    ca_al_char_s: float
    vtt_video_id: str
    ref_chars: int | None
    ca_al_vtt_refrate_char_s: float | None
    ca_al_char_s_gapexcl: float
    num_excluded_gaps: int
    excluded_gap_total_s: float


def analyze_experiment(path: Path) -> AlReport | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    if not events:
        return None
    input_cfg = data.get("input") or {}
    duration = _source_duration_seconds(input_cfg, events)
    if not duration or duration <= 0:
        return None
    span = _speech_span(events)
    t0 = span[0] if span is not None else 0.0

    global_text_parts: list[str] = []
    global_settle: list[float] = []
    for utt_span in _translation_spans(events):
        final_text = utt_span[-1][1]
        if not final_text:
            continue
        global_text_parts.append(final_text)
        global_settle.extend(settle_times(final_text, utt_span))

    n = sum(len(t) for t in global_text_parts)
    if n == 0:
        return None
    gamma = n / duration
    t_end = t0 + duration

    tau = n
    for idx, t in enumerate(global_settle, start=1):
        if t >= t_end:
            tau = idx
            break

    lags = [(global_settle[i - 1] - t0) - (i - 1) / gamma for i in range(1, tau + 1)]
    ca_al = sum(lags) / len(lags)

    ref = _vtt_reference(input_cfg, duration)
    vtt_video_id, ref_chars, ca_al_vtt_refrate = "", None, None
    if ref and ref[1] > 0:
        vtt_video_id, ref_chars = ref
        gamma_ref = ref_chars / duration
        lags_ref = [
            (global_settle[i - 1] - t0) - (i - 1) / gamma_ref for i in range(1, tau + 1)
        ]
        ca_al_vtt_refrate = sum(lags_ref) / len(lags_ref)

    intervals = _asr_utterance_intervals(events)
    excluded = _excluded_gaps(intervals, GAP_EXCLUSION_THRESHOLD_S)
    excluded_total = sum(g.excluded_s for g in excluded)
    duration_gapexcl = duration - excluded_total
    if excluded and duration_gapexcl > 0:
        gamma_gapexcl = n / duration_gapexcl
        settle_gapexcl = [
            (t - t0) - _gap_exclusion_offset(excluded, t) for t in global_settle
        ]
        tau_gapexcl = n
        for idx, d in enumerate(settle_gapexcl, start=1):
            if d >= duration_gapexcl:
                tau_gapexcl = idx
                break
        lags_gapexcl = [
            settle_gapexcl[i - 1] - (i - 1) / gamma_gapexcl
            for i in range(1, tau_gapexcl + 1)
        ]
        ca_al_gapexcl = sum(lags_gapexcl) / len(lags_gapexcl)
    else:
        ca_al_gapexcl = ca_al

    return AlReport(
        json_path=str(path),
        experiment_name=data.get("experiment_name", path.stem),
        domain=data.get("domain", ""),
        source_duration_s=duration,
        output_chars=n,
        num_spans=len(global_text_parts),
        gamma_chars_per_s=gamma,
        tau_char_index=tau,
        ca_al_char_s=ca_al,
        vtt_video_id=vtt_video_id,
        ref_chars=ref_chars,
        ca_al_vtt_refrate_char_s=ca_al_vtt_refrate,
        ca_al_char_s_gapexcl=ca_al_gapexcl,
        num_excluded_gaps=len(excluded),
        excluded_gap_total_s=excluded_total,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=None)
    parser.add_argument(
        "--out-csv", type=Path, default=Path("experiments/simul_latency_metrics.csv")
    )
    args = parser.parse_args()

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    reports: list[AlReport] = []
    for path in paths:
        try:
            r = analyze_experiment(path)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")
            continue
        if r is not None:
            reports.append(r)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "domain",
                "source_duration_s",
                "output_chars",
                "num_spans",
                "gamma_chars_per_s",
                "tau_char_index",
                "ca_al_char_s",
                "vtt_video_id",
                "ref_chars",
                "ca_al_vtt_refrate_char_s",
                "ca_al_char_s_gapexcl",
                "num_excluded_gaps",
                "excluded_gap_total_s",
            ]
        )
        for r in reports:
            writer.writerow(
                [
                    r.experiment_name,
                    r.json_path,
                    r.domain,
                    r.source_duration_s,
                    r.output_chars,
                    r.num_spans,
                    r.gamma_chars_per_s,
                    r.tau_char_index,
                    r.ca_al_char_s,
                    r.vtt_video_id,
                    r.ref_chars,
                    r.ca_al_vtt_refrate_char_s,
                    r.ca_al_char_s_gapexcl,
                    r.num_excluded_gaps,
                    r.excluded_gap_total_s,
                ]
            )

    print(
        f"analyzed {len(reports)} experiment(s) with events+duration -> {args.out_csv}"
    )
    al_values = [r.ca_al_char_s for r in reports]
    if al_values:
        print(
            f"ca_al_char_s across {len(al_values)} run(s): "
            f"mean={mean(al_values):.3f}s median={median(al_values):.3f}s "
            f"min={min(al_values):.3f}s max={max(al_values):.3f}s "
            "(cf. SimulSeamless IWSLT2024 En->Ja computation-aware AL 4.018s)"
        )
    refrate_values = [
        r.ca_al_vtt_refrate_char_s
        for r in reports
        if r.ca_al_vtt_refrate_char_s is not None
    ]
    if refrate_values:
        print(
            f"ca_al_vtt_refrate_char_s (reference-caption-rate ideal line, "
            "NOT LAAL -- see module docstring) across "
            f"{len(refrate_values)} run(s) with a matching .ja.vtt: "
            f"mean={mean(refrate_values):.3f}s median={median(refrate_values):.3f}s"
        )
    gapexcl_values = [r.ca_al_char_s_gapexcl for r in reports]
    affected = [r for r in reports if r.num_excluded_gaps > 0]
    if gapexcl_values:
        print(
            f"ca_al_char_s_gapexcl (mid-run gap exclusion, threshold="
            f"{GAP_EXCLUSION_THRESHOLD_S}s -- see module docstring) across "
            f"{len(gapexcl_values)} run(s): mean={mean(gapexcl_values):.3f}s "
            f"median={median(gapexcl_values):.3f}s; {len(affected)} run(s) had "
            "at least one excluded gap"
        )


if __name__ == "__main__":
    main()
