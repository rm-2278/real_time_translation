"""Turning translated text into readable captions.

- `subtitle_formatter`: offline formatting of a finished experiment JSON into
  timed cards (wrapping, merging, reading-speed scheduling).
- `live_presenter`: the same rules applied in real time for the live overlay.

Measuring caption quality (readability, latency) is not here; those are
analyzers and live in `experiments/`.
"""
