"""Render a video with burned-in captions from an experiment JSON.

Not a translation-accuracy experiment in the CLAUDE.md logging-rule sense --
this is an engineering/readability demo tool: it takes an already-recorded
experiment JSON (real ASR + translation, real timestamps) and produces an
actual video file with subtitles burned in, so caption *presentation*
choices (naive dump vs. line-wrapped/re-timed) can be watched and compared,
not just read as numbers.

The local ffmpeg build here has no libass/drawtext (a slimmed Homebrew
bottle), so captions are rendered as transparent PNG frames (Pillow, direct
TTF/TTC font file -- no fontconfig needed) and composited with ffmpeg's core
`overlay` filter instead of the `subtitles` filter.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from real_time_translation.captions.subtitle_formatter import (
    Cue,
    extract_utterance_cues,
    format_naive,
    format_readable,
    write_srt,
)

CANVAS_W, CANVAS_H = 1280, 720
FONT_PATH = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"
FONT_INDEX = 0
FONT_SIZE = 42
LINE_GAP = 12
BOTTOM_MARGIN = 90
PAD_X, PAD_Y = 24, 14


def _load_experiment(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


SAFE_MARGIN = 24  # a caption is allowed to be ugly, never invisible off-canvas
MAX_TEXT_WIDTH = CANVAS_W - 2 * SAFE_MARGIN


def _render_cue_png(
    cue: Cue,
    out_path: Path,
    *,
    shrink_to_fit: bool,
    style: str = "boxed",
) -> None:
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    lines = cue.text.split("\n")

    font = ImageFont.truetype(FONT_PATH, size=FONT_SIZE, index=FONT_INDEX)
    if shrink_to_fit:
        # Readable mode has no business overflowing the frame at all -- if
        # the widest line still doesn't fit at the base size (possible for
        # a heavily-kanji chunk near the character budget), shrink until it
        # does rather than let SAFE_MARGIN clamp text into an unreadable
        # pile-up at the edge.
        size = FONT_SIZE
        while size > 20:
            candidate = ImageFont.truetype(FONT_PATH, size=size, index=FONT_INDEX)
            widest = max(
                draw.textbbox((0, 0), line, font=candidate)[2] for line in lines
            )
            if widest <= MAX_TEXT_WIDTH:
                font = candidate
                break
            size -= 2
        else:
            font = ImageFont.truetype(FONT_PATH, size=20, index=FONT_INDEX)

    line_sizes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    line_heights = [b[3] - b[1] for b in line_sizes]
    line_widths = [b[2] - b[0] for b in line_sizes]
    block_h = sum(line_heights) + LINE_GAP * (len(lines) - 1)
    block_w = max(line_widths) if line_widths else 0

    box_top = CANVAS_H - BOTTOM_MARGIN - block_h - PAD_Y * 2
    box_left = (CANVAS_W - block_w) // 2 - PAD_X
    box_right = (CANVAS_W + block_w) // 2 + PAD_X
    box_bottom = CANVAS_H - BOTTOM_MARGIN + PAD_Y
    # An unwrapped line can be wider than the canvas (that's the point of
    # the "naive" demo mode) -- keep the background box itself on-screen so
    # a viewer sees "caption box, text overflowing it" instead of the whole
    # thing silently vanishing past the frame edge.
    box_left = max(SAFE_MARGIN, box_left)
    box_right = min(CANVAS_W - SAFE_MARGIN, box_right)
    if style == "boxed":
        # Netflix/broadcast convention: solid semi-opaque background plate.
        draw.rounded_rectangle(
            [box_left, box_top, box_right, box_bottom],
            radius=10,
            fill=(10, 16, 15, 190),
        )

    y = box_top + PAD_Y
    for line, (_l, t, _r, b), w in zip(lines, line_sizes, line_widths, strict=True):
        x = max(SAFE_MARGIN, (CANVAS_W - w) // 2)
        if style == "outline":
            # YouTube auto-caption convention: no plate, just a heavy black
            # stroke around white text for legibility over any background.
            draw.text(
                (x, y - t),
                line,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=3,
                stroke_fill=(0, 0, 0, 235),
            )
        else:
            draw.text((x, y - t), line, font=font, fill=(255, 255, 255, 255))
        y += (b - t) + LINE_GAP

    img.save(out_path)


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


# ffmpeg's overlay-per-PNG-file approach opens one decoder per cue; past
# roughly this many simultaneous inputs, ffmpeg starts failing with
# "Error while opening decoder: Resource temporarily unavailable" (found
# 2026-09-18 rendering a 75-minute/2691-cue lecture -- failed consistently
# around input #372 even after raising `ulimit -n`, so it isn't purely a
# file-descriptor limit; some other per-process resource, e.g. threads,
# is the real ceiling). Long recordings get chunked into separate ffmpeg
# passes of at most this many cues each, then concatenated -- see
# `_render_chunk` / the chunking loop in `render()`.
_MAX_CUES_PER_PASS = 200
_CHUNK_SECONDS = 240.0


def _render_chunk(
    cues: list[Cue],
    *,
    chunk_video_length: float,
    source_path: Path,
    abs_start_seconds: float,
    abs_source_seconds_available: float,
    abs_source_end_seconds: float,
    full_audio_path: Path,
    audio_offset: float,
    style: str,
    shrink_to_fit: bool,
    background: str,
    out_path: Path,
) -> None:
    """Render one ffmpeg pass covering `chunk_video_length` seconds of
    overlay timeline, with `cues` already shifted to be relative to this
    chunk's own start (0 = this chunk's first frame)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        chunk_audio_path = tmp_path / "audio.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(audio_offset),
                "-t",
                str(chunk_video_length),
                "-i",
                str(full_audio_path),
                str(chunk_audio_path),
            ],
            check=True,
            capture_output=True,
        )

        png_paths: list[Path] = []
        for i, cue in enumerate(cues):
            png_path = tmp_path / f"cue_{i:04d}.png"
            _render_cue_png(cue, png_path, shrink_to_fit=shrink_to_fit, style=style)
            png_paths.append(png_path)

        if background == "video" and abs_source_seconds_available > 0:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(abs_start_seconds),
                "-t",
                str(abs_source_seconds_available),
                "-i",
                str(source_path),
            ]
        elif background == "video":
            # This chunk is entirely past the source clip's own real
            # duration (a translation-latency tail past the last frame of
            # actual footage) -- grab one still frame to freeze instead of
            # a zero-length extraction, which ffmpeg would reject.
            freeze_at = max(0.0, min(abs_start_seconds, abs_source_end_seconds) - 0.1)
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(freeze_at),
                "-t",
                "0.1",
                "-i",
                str(source_path),
            ]
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x14201d:s={CANVAS_W}x{CANVAS_H}:d={chunk_video_length}",
            ]
        for p in png_paths:
            cmd += ["-i", str(p)]
        cmd += ["-i", str(chunk_audio_path)]

        filter_parts = []
        if background == "video":
            extracted = (
                abs_source_seconds_available
                if abs_source_seconds_available > 0
                else 0.1
            )
            pad_tail = max(0.0, chunk_video_length - extracted)
            filter_parts.append(
                f"[0:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=decrease,"
                f"pad={CANVAS_W}:{CANVAS_H}:(ow-iw)/2:(oh-ih)/2:color=0x14201d,"
                f"tpad=stop_mode=clone:stop_duration={pad_tail:.3f}[base]"
            )
            last_label = "base"
        else:
            last_label = "0:v"
        for i, cue in enumerate(cues):
            in_idx = i + 1
            out_label = f"v{i}"
            filter_parts.append(
                f"[{last_label}][{in_idx}:v]overlay=0:0:"
                f"enable='between(t,{cue.start:.3f},{cue.end:.3f})'[{out_label}]"
            )
            last_label = out_label
        filter_complex = ";".join(filter_parts)
        audio_idx = len(png_paths) + 1

        cmd += [
            "-filter_complex",
            filter_complex,
            "-map",
            f"[{last_label}]",
            "-map",
            f"{audio_idx}:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            sys.stderr.write(result.stderr[-4000:])
            raise SystemExit("ffmpeg render failed")


def render(
    experiment_path: Path,
    mode: str,
    name: str,
    output_dir: Path,
    style: str = "outline",
    background: str = "color",
) -> tuple[Path, Path]:
    data = _load_experiment(experiment_path)
    events = data["results"]["events"]
    source_path = Path(data["input"]["path"])
    start_seconds = float(data["input"]["start_seconds"])
    raw_duration = data["input"]["duration_seconds"]
    if raw_duration is not None:
        duration_seconds = float(raw_duration)
    else:
        # "process rest of file" runs (no --duration passed) save
        # duration_seconds=None -- fall back to how much audio was
        # actually transcribed (the last event's own asr_end_time), which
        # is more accurate than re-probing the source file's full length
        # (the run may have stopped short of the file's actual end).
        end_times = [
            e["asr_end_time"] for e in events if e.get("asr_end_time") is not None
        ]
        if not end_times:
            raise SystemExit(
                f"{experiment_path}: duration_seconds is None and no event "
                "has an asr_end_time to fall back on"
            )
        duration_seconds = max(end_times)

    raw_cues = extract_utterance_cues(events)
    cues = format_naive(raw_cues) if mode == "naive" else format_readable(raw_cues)
    if not cues:
        raise SystemExit(f"No translation cues extracted from {experiment_path}")

    # A translation can genuinely arrive after the clip's own speech audio
    # ends (real end-to-end latency -- see this repo's own latency
    # findings), and `format_readable` can deliberately hold a card past
    # where naive arrival would've cut it. Size the rendered video to fit
    # every cue instead of silently dropping/truncating whatever runs past
    # a fixed `duration_seconds` -- that previously made a fully-translated
    # tail look untranslated just because it displayed a few seconds late.
    video_length = max(duration_seconds, cues[-1].end + 0.5)

    output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = output_dir / f"{name}.srt"
    write_srt(cues, srt_path)
    video_out = output_dir / f"{name}.mp4"
    shrink_to_fit = mode != "naive"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        full_audio_path = tmp_path / "full_audio.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(start_seconds),
                "-t",
                str(duration_seconds),
                "-i",
                str(source_path),
                "-vn",
                "-ac",
                "2",
                "-ar",
                "44100",
                "-af",
                f"apad=whole_dur={video_length}",
                str(full_audio_path),
            ],
            check=True,
            capture_output=True,
        )

        if len(cues) <= _MAX_CUES_PER_PASS:
            _render_chunk(
                cues,
                chunk_video_length=video_length,
                source_path=source_path,
                abs_start_seconds=start_seconds,
                abs_source_seconds_available=duration_seconds,
                abs_source_end_seconds=start_seconds + duration_seconds,
                full_audio_path=full_audio_path,
                audio_offset=0.0,
                style=style,
                shrink_to_fit=shrink_to_fit,
                background=background,
                out_path=video_out,
            )
        else:
            chunk_paths: list[Path] = []
            chunk_start = 0.0
            idx = 0
            while chunk_start < video_length:
                chunk_len = min(_CHUNK_SECONDS, video_length - chunk_start)
                chunk_end = chunk_start + chunk_len
                chunk_cues = [
                    Cue(
                        c.start - chunk_start,
                        min(c.end, chunk_end) - chunk_start,
                        c.text,
                    )
                    for c in cues
                    if chunk_start <= c.start < chunk_end
                ]
                if chunk_cues:
                    # abs_source_seconds_available: how much of THIS
                    # chunk's window actually still has real source
                    # footage/audio left (0 once we're past
                    # duration_seconds, into pure caption-latency tail).
                    abs_available = max(
                        0.0, min(chunk_len, duration_seconds - chunk_start)
                    )
                    chunk_path = tmp_path / f"chunk_{idx:04d}.mp4"
                    _render_chunk(
                        chunk_cues,
                        chunk_video_length=chunk_len,
                        source_path=source_path,
                        abs_start_seconds=start_seconds + chunk_start,
                        abs_source_seconds_available=abs_available,
                        abs_source_end_seconds=start_seconds + duration_seconds,
                        full_audio_path=full_audio_path,
                        audio_offset=chunk_start,
                        style=style,
                        shrink_to_fit=shrink_to_fit,
                        background=background,
                        out_path=chunk_path,
                    )
                    chunk_paths.append(chunk_path)
                chunk_start += chunk_len
                idx += 1

            concat_list = tmp_path / "concat_list.txt"
            concat_list.write_text(
                "".join(f"file '{p.resolve()}'\n" for p in chunk_paths)
            )
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list),
                    "-c",
                    "copy",
                    str(video_out),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                sys.stderr.write(result.stderr[-4000:])
                raise SystemExit("ffmpeg concat of chunked render failed")

    return video_out, srt_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True, type=Path)
    parser.add_argument("--mode", choices=["naive", "readable"], required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("experiments/caption_demos")
    )
    # outline (YouTube-caption look) beat the boxed plate in user review
    # 2026-09-15 -- kept as the default; boxed is still available via --style.
    parser.add_argument("--style", choices=["boxed", "outline"], default="outline")
    parser.add_argument(
        "--background",
        choices=["color", "video"],
        default="color",
        help="'video' letterboxes the real source clip instead of a solid "
        "color canvas (frozen on the last frame for any caption tail that "
        "runs past the clip's own duration).",
    )
    args = parser.parse_args(argv)

    video_path, srt_path = render(
        args.experiment,
        args.mode,
        args.name,
        args.output_dir,
        style=args.style,
        background=args.background,
    )
    print(f"Wrote: {video_path}")
    print(f"Wrote: {srt_path}")


if __name__ == "__main__":
    main()
