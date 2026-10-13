"""Small descriptive-statistics helpers shared by the experiment analyzers.

Every analyzer reports "no data" as `None` rather than raising, so a report
over an empty experiment still renders; `statistics.mean([])` would raise
instead, which is why these are not just re-exports of the stdlib.
"""

from __future__ import annotations

import math
import statistics


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def stdev(values: list[float]) -> float | None:
    """Sample standard deviation; None below two points (undefined)."""
    return statistics.stdev(values) if len(values) > 1 else None


def pearson_r(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation; None if undefined (n < 2 or zero variance)."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / math.sqrt(var_x * var_y)
