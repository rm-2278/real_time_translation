"""Automated caption-readability scoring against real broadcast standards.

Grounded in Netflix's published Japanese Timed Text Style Guide (4 CPS,
<=13 full-width characters/line, 5/6s-7s display window, 2-frame minimum
gap) and the BBC Subtitle Guidelines v1.2.3 (June 2024; max 2 lines,
~0.3s/word minimum hold). This gives every caption-formatting experiment
in this repo (subtitle_formatter.py's own output, or any other tool's SRT)
a single, reproducible, human-judgment-free score: what fraction of cues
violate each real standard, and by how much on average -- so two
formatting approaches can be quantitatively ranked, not just eyeballed.

CLI: `real-time-translation-caption-readability-score --srt <path>`
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from real_time_translation.captions.subtitle_formatter import Cue, _weighted_len

DEFAULT_MAX_CPS = 4.0
DEFAULT_MAX_CHARS_PER_LINE = 13
DEFAULT_MAX_LINES = 2
DEFAULT_MIN_DURATION = 5 / 6
_PARTICLES = "はがをにでともへやのからまでよりねよわ"


@dataclass(frozen=True)
class ReadabilityReport:
    n_cues: int
    mean_cps: float
    cps_violations: int
    cps_violation_rate: float
    line_length_violations: int
    line_length_violation_rate: float
    line_count_violations: int
    line_count_violation_rate: float
    duration_violations: int
    duration_violation_rate: float
    particle_start_violations: int
    particle_start_violation_rate: float
    any_violation_rate: float


def parse_srt(path: Path) -> list[Cue]:
    """Minimal SRT reader (stdlib only) -- the inverse of write_srt."""
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", text.strip())
    ts_re = re.compile(
        r"(\d\d):(\d\d):(\d\d),(\d\d\d)\s*-->\s*(\d\d):(\d\d):(\d\d),(\d\d\d)"
    )

    def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    cues: list[Cue] = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue
        m = ts_re.search(lines[1])
        if not m:
            continue
        start = _to_seconds(*m.groups()[0:4])
        end = _to_seconds(*m.groups()[4:8])
        text = "\n".join(lines[2:])
        cues.append(Cue(start, end, text))
    return cues


def analyze_readability(
    cues: list[Cue],
    *,
    max_cps: float = DEFAULT_MAX_CPS,
    max_chars_per_line: int = DEFAULT_MAX_CHARS_PER_LINE,
    max_lines: int = DEFAULT_MAX_LINES,
    min_duration: float = DEFAULT_MIN_DURATION,
) -> ReadabilityReport:
    n = len(cues)
    if n == 0:
        return ReadabilityReport(0, 0.0, 0, 0.0, 0, 0.0, 0, 0.0, 0, 0.0, 0, 0.0, 0.0)

    cps_values: list[float] = []
    cps_bad = 0
    line_len_bad = 0
    line_count_bad = 0
    duration_bad = 0
    particle_bad = 0
    any_bad = 0

    for cue in cues:
        duration = max(cue.end - cue.start, 1e-6)
        weighted = _weighted_len(cue.text.replace("\n", ""))
        cps = weighted / duration
        cps_values.append(cps)

        violated = False
        if cps > max_cps + 1e-6:  # a formatter that targets max_cps exactly
            cps_bad += 1  # will land float-noise over it; don't count that
            violated = True

        lines = cue.text.split("\n")
        if len(lines) > max_lines:
            line_count_bad += 1
            violated = True
        if any(len(line) > max_chars_per_line for line in lines):
            line_len_bad += 1
            violated = True
        if duration < min_duration:
            duration_bad += 1
            violated = True
        if any(line and line[0] in _PARTICLES for line in lines[1:]):
            particle_bad += 1
            violated = True
        if violated:
            any_bad += 1

    return ReadabilityReport(
        n_cues=n,
        mean_cps=sum(cps_values) / n,
        cps_violations=cps_bad,
        cps_violation_rate=cps_bad / n,
        line_length_violations=line_len_bad,
        line_length_violation_rate=line_len_bad / n,
        line_count_violations=line_count_bad,
        line_count_violation_rate=line_count_bad / n,
        duration_violations=duration_bad,
        duration_violation_rate=duration_bad / n,
        particle_start_violations=particle_bad,
        particle_start_violation_rate=particle_bad / n,
        any_violation_rate=any_bad / n,
    )


def format_report(name: str, report: ReadabilityReport) -> str:
    return (
        f"{name}: n={report.n_cues} "
        f"mean_cps={report.mean_cps:.2f} (limit {DEFAULT_MAX_CPS}) "
        f"cps_viol={report.cps_violation_rate:.0%} "
        f"line_len_viol={report.line_length_violation_rate:.0%} "
        f"line_count_viol={report.line_count_violation_rate:.0%} "
        f"duration_viol={report.duration_violation_rate:.0%} "
        f"particle_start_viol={report.particle_start_violation_rate:.0%} "
        f"any_viol={report.any_violation_rate:.0%}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--srt", required=True, type=Path, nargs="+")
    args = parser.parse_args(argv)

    for srt_path in args.srt:
        cues = parse_srt(srt_path)
        report = analyze_readability(cues)
        print(format_report(srt_path.name, report))


if __name__ == "__main__":
    main()
