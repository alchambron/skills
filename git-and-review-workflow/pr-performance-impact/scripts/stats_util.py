#!/usr/bin/env python3
"""Pure-stdlib scientific statistics for interleaved A/B performance benchmarks.

Computes median, mean, IQR, stdev, p95, percentage delta, Cliff's delta effect
size, and two-sided Mann-Whitney U p-value with tie correction.
Compatible with Python 3.9+.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (pct / 100.0) * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def iqr(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    return percentile(values, 75.0) - percentile(values, 25.0)


def summarize_samples(values: List[float]) -> Dict[str, Any]:
    nums = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not nums:
        return {"n": 0, "median": None, "mean": None, "iqr": None, "stdev": None, "p95": None}
    return {
        "n": len(nums),
        "median": round(statistics.median(nums), 3),
        "mean": round(statistics.mean(nums), 3),
        "iqr": round(iqr(nums), 3),
        "stdev": round(statistics.stdev(nums), 3) if len(nums) >= 2 else 0.0,
        "p95": round(percentile(nums, 95.0), 3),
    }


def mann_whitney_u_pvalue(a: List[float], b: List[float]) -> float:
    """Two-sided Mann-Whitney U p-value with tie correction (normal approx)."""
    n1 = len(a)
    n2 = len(b)
    if n1 < 2 or n2 < 2:
        return 1.0

    combined = [(val, 0) for val in a] + [(val, 1) for val in b]
    combined.sort(key=lambda x: x[0])

    # Assign average ranks for ties.
    ranks_a = 0.0
    tie_term = 0.0
    i = 0
    n = n1 + n2
    while i < n:
        j = i + 1
        while j < n and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        tie_count = j - i
        if tie_count > 1:
            tie_term += (tie_count ** 3 - tie_count)
        for k in range(i, j):
            if combined[k][1] == 0:
                ranks_a += avg_rank
        i = j

    u1 = ranks_a - (n1 * (n1 + 1)) / 2.0
    u2 = (n1 * n2) - u1
    u = min(u1, u2)

    mean_u = (n1 * n2) / 2.0
    if n <= 1:
        return 1.0
    var_u = (n1 * n2 / 12.0) * ((n + 1.0) - (tie_term / (n * (n - 1.0))))
    if var_u <= 0.0:
        return 1.0

    # Continuity correction for discrete U distribution.
    z = (abs(u - mean_u) - 0.5) / math.sqrt(var_u)
    if z <= 0.0:
        return 1.0
    p_val = math.erfc(z / math.sqrt(2.0))
    return max(0.0, min(1.0, p_val))


def cliffs_delta(base: List[float], pr: List[float]) -> float:
    """Non-parametric effect size in [-1, 1]. Positive means base > pr (lower/faster in PR)."""
    if not base or not pr:
        return 0.0
    greater = 0
    less = 0
    for x in base:
        for y in pr:
            if x > y:
                greater += 1
            elif x < y:
                less += 1
    return (greater - less) / (len(base) * len(pr))


def compare_ab(
    base_values: List[float],
    pr_values: List[float],
    lower_is_better: bool = True,
    alpha: float = 0.05,
    min_practical_pct: float = 2.0,
) -> Dict[str, Any]:
    """Scientifically compare paired/interleaved A/B measurement samples."""
    b_nums = [float(v) for v in base_values if v is not None and math.isfinite(float(v))]
    p_nums = [float(v) for v in pr_values if v is not None and math.isfinite(float(v))]
    b_sum = summarize_samples(b_nums)
    p_sum = summarize_samples(p_nums)

    if len(b_nums) < 3 or len(p_nums) < 3:
        return {
            "base": b_sum,
            "pr": p_sum,
            "improvement_pct": None,
            "p_value": None,
            "cliffs_delta": None,
            "verdict": "insufficient_samples",
            "significant": False,
        }

    b_med = b_sum["median"]
    p_med = p_sum["median"]
    if b_med == 0:
        imp_pct = 0.0 if p_med == 0 else (-100.0 if lower_is_better else 100.0)
    else:
        raw = ((b_med - p_med) / b_med) * 100.0
        imp_pct = raw if lower_is_better else -raw

    p_val = mann_whitney_u_pvalue(b_nums, p_nums)
    effect = cliffs_delta(b_nums, p_nums)
    if not lower_is_better:
        effect = -effect

    significant = (p_val < alpha) and (abs(imp_pct) >= min_practical_pct) and (abs(effect) >= 0.33)
    if significant and imp_pct > 0:
        verdict = "significant_improvement"
    elif significant and imp_pct < 0:
        verdict = "significant_regression"
    else:
        verdict = "no_significant_change"

    return {
        "base": b_sum,
        "pr": p_sum,
        "improvement_pct": round(imp_pct, 2),
        "p_value": round(p_val, 4),
        "cliffs_delta": round(effect, 3),
        "verdict": verdict,
        "significant": significant,
    }
