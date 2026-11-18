"""Dependency-free statistics: Student-t quantiles/CDF, Welch's t-test, Wilson intervals.

Guard rail: conclusions are only labelled significant when both arms reach `min_sample_size`
AND the confidence interval of the difference excludes zero.
"""

from __future__ import annotations

import math
from statistics import NormalDist, mean, stdev


def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-30

    def nz(v: float) -> float:
        return v if abs(v) > tiny else tiny

    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 / nz(1 - qab * x / qap)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 / nz(1 + aa * d)
        c = nz(1 + aa / c)
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 / nz(1 + aa * d)
        c = nz(1 + aa / c)
        de = d * c
        h *= de
        if abs(de - 1) < 3e-12:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a,b) (Numerical Recipes 6.4)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1 - math.exp(lbeta) * _betacf(b, a, 1 - x) / b


def t_sf_two_sided(t: float, df: float) -> float:
    return betainc(df / 2, 0.5, df / (df + t * t))


def t_quantile(p: float, df: float) -> float:
    """Inverse Student-t CDF via bisection on the exact CDF."""
    if df > 1e6:
        return NormalDist().inv_cdf(p)
    target = 2 * (1 - p)  # two-sided tail mass for upper quantile
    lo, hi = 0.0, 1000.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_sf_two_sided(mid, df) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def mean_ci(xs: list[float], conf: float = 0.95) -> dict:
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "ci_low": None, "ci_high": None}
    m = mean(xs)
    if n < 2:
        return {"n": n, "mean": m, "sd": None, "ci_low": None, "ci_high": None}
    sd = stdev(xs)
    half = t_quantile(1 - (1 - conf) / 2, n - 1) * sd / math.sqrt(n)
    return {"n": n, "mean": m, "sd": sd, "ci_low": m - half, "ci_high": m + half}


def wilson(successes: int, n: int, conf: float = 0.95) -> dict:
    if n == 0:
        return {"rate": None, "ci_low": None, "ci_high": None, "n": 0}
    z = NormalDist().inv_cdf(1 - (1 - conf) / 2)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {"rate": p, "ci_low": max(0.0, centre - half), "ci_high": min(1.0, centre + half), "n": n}


def welch(a: list[float], b: list[float], conf: float = 0.95) -> dict:
    """Difference of means (b - a) with Welch-Satterthwaite df."""
    if len(a) < 2 or len(b) < 2:
        return {"diff": None, "ci_low": None, "ci_high": None, "p_value": None, "df": None}
    ma, mb = mean(a), mean(b)
    va, vb = stdev(a) ** 2 / len(a), stdev(b) ** 2 / len(b)
    se = math.sqrt(va + vb)
    diff = mb - ma
    if se == 0:
        return {"diff": diff, "ci_low": diff, "ci_high": diff, "p_value": 0.0 if diff else 1.0, "df": None}
    df = (va + vb) ** 2 / ((va**2) / (len(a) - 1) + (vb**2) / (len(b) - 1))
    t = diff / se
    q = t_quantile(1 - (1 - conf) / 2, df)
    return {"diff": diff, "ci_low": diff - q * se, "ci_high": diff + q * se, "p_value": t_sf_two_sided(abs(t), df),
            "df": df, "t": t}


def verdict(diff: dict, n_a: int, n_b: int, min_n: int, higher_is_better: bool = True) -> dict:
    if n_a < min_n or n_b < min_n:
        return {"significant": False, "conclusion": "insufficient_sample",
                "message": f"Need at least {min_n} observations per arm (have {n_a} and {n_b}). "
                           "Results are directional only and must not be treated as statistically significant."}
    if diff.get("ci_low") is None:
        return {"significant": False, "conclusion": "insufficient_variance", "message": "Not enough variance to test."}
    excludes_zero = diff["ci_low"] > 0 or diff["ci_high"] < 0
    if not excludes_zero:
        return {"significant": False, "conclusion": "no_detectable_difference",
                "message": "95% CI of the difference includes zero."}
    better = (diff["diff"] > 0) == higher_is_better
    return {"significant": True, "conclusion": "treatment_better" if better else "treatment_worse",
            "message": f"95% CI excludes zero (p={diff['p_value']:.4f})."}
