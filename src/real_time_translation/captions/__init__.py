"""Post-hoc caption formatting and rendering tools.

Distinct from the live pipeline's real-time subtitle stream: these tools take
a finished experiment JSON (real ASR + translation events, already recorded)
and turn it into a clean, readable subtitle track for offline review/demo
purposes -- e.g. burning captions onto a rendered video to evaluate
readability choices (line length, reading speed, avoiding mid-sentence
fragments) side by side.
"""
