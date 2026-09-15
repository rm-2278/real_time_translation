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


def render(
    experiment_path: Path,
    mode: str,
    name: str,
    output_dir: Path,
    style: str = "boxed",
) -> tuple[Path, Path]:
    data = _load_experiment(experiment_path)
    events = data["results"]["events"]
    source_path = Path(data["input"]["path"])
    start_seconds = float(data["input"]["start_seconds"])
    duration_seconds = float(data["input"]["duration_seconds"])

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

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        audio_path = tmp_path / "audio.wav"
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
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )
        audio_duration = _ffprobe_duration(audio_path)

        shrink_to_fit = mode != "naive"
        png_paths: list[Path] = []
        for i, cue in enumerate(cues):
            png_path = tmp_path / f"cue_{i:04d}.png"
            _render_cue_png(cue, png_path, shrink_to_fit=shrink_to_fit, style=style)
            png_paths.append(png_path)

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x14201d:s={CANVAS_W}x{CANVAS_H}:d={audio_duration}",
        ]
        for p in png_paths:
            cmd += ["-i", str(p)]
        cmd += ["-i", str(audio_path)]

        filter_parts = []
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

        video_out = output_dir / f"{name}.mp4"
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
            str(video_out),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            sys.stderr.write(result.stderr[-4000:])
            raise SystemExit("ffmpeg render failed")

    return video_out, srt_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True, type=Path)
    parser.add_argument("--mode", choices=["naive", "readable"], required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("experiments/caption_demos")
    )
    parser.add_argument("--style", choices=["boxed", "outline"], default="boxed")
    args = parser.parse_args(argv)

    video_path, srt_path = render(
        args.experiment, args.mode, args.name, args.output_dir, style=args.style
    )
    print(f"Wrote: {video_path}")
    print(f"Wrote: {srt_path}")


if __name__ == "__main__":
    main()
