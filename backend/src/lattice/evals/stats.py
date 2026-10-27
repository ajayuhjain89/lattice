"""Small, dependency-free statistics shared by the evaluation harnesses (P13.1).

Every rate the harnesses report travels with its denominator; intervals are Wilson
score intervals (95%), which stay meaningful for the small slices of the golden set
(a 0/13 or 12/13 rate is not a point estimate to trust on its own).
"""

import math
from collections.abc import Sequence
from typing import Any

Z95 = 1.959964


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (the harnesses' long-standing definition)."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 2)


def distribution(values: Sequence[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
        "max": round(max(values), 2) if values else None,
    }


def wilson(successes: int, n: int) -> list[float] | None:
    """95% Wilson score interval for ``successes`` out of ``n``."""
    if n == 0:
        return None
    p = successes / n
    denominator = 1 + Z95**2 / n
    centre = (p + Z95**2 / (2 * n)) / denominator
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95**2 / (4 * n * n)) / denominator
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def proportion(successes: int, n: int) -> dict[str, Any]:
    """A rate with its numerator, denominator and 95% interval."""
    return {
        "count": successes,
        "n": n,
        "rate": round(successes / n, 4) if n else None,
        "ci95": wilson(successes, n),
    }
