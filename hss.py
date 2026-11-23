# -*- coding: utf-8 -*-
"""Rainfall frequency analysis (statistics, distributions, Chi-square and Kolmogorov-Smirnov tests).

Pure Python (math only): no numpy / scipy needed, no QGIS dependency.
"""
import bisect
import math

EULER = 0.5772156649015329
DIST_NORMAL = "Normal"
DIST_LOGNORMAL = "Log Normal"
DIST_GUMBEL = "Gumbel"
DIST_LP3 = "Log Pearson III"


# ----------------------------------------------------------------------------
# Descriptive statistics
# ----------------------------------------------------------------------------
def mean(x):
    return sum(x) / len(x)


def std(x):
    """Sample standard deviation (n - 1)."""
    m = mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def skewness(x):
    """Cs = n * sum((x-m)^3) / ((n-1)(n-2) s^3)  (coefficient of skewness, as used in Indonesian practice)."""
    n = len(x)
    m, s = mean(x), std(x)
    if s == 0 or n < 3:
        return 0.0
    return n * sum((v - m) ** 3 for v in x) / ((n - 1) * (n - 2) * s ** 3)


def kurtosis(x):
    """Ck = n^2 * sum((x-m)^4) / ((n-1)(n-2)(n-3) s^4)  (normal distribution = 3)."""
    n = len(x)
    m, s = mean(x), std(x)
    if s == 0 or n < 4:
        return 0.0
    return n * n * sum((v - m) ** 4 for v in x) / ((n - 1) * (n - 2) * (n - 3) * s ** 4)


# ----------------------------------------------------------------------------
# Special functions
# ----------------------------------------------------------------------------
def norm_cdf(z):
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


_A = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
_B = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01, -1.328068155288572e01]
_C = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
_D = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]


def norm_ppf(p):
    """Inverse standard normal CDF (Acklam's algorithm + one Halley refinement)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        x = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1)
    elif p > 1 - plow:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1)
    else:
        q = p - 0.5
        r = q * q
        x = (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / (
            ((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1)
    e = norm_cdf(x) - p
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def gammp(a, x):
    """Regularized lower incomplete gamma function P(a, x)."""
    if x <= 0:
        return 0.0
    lg = math.lgamma(a)
    if x < a + 1:
        ap, s = a, 1.0 / a
        d = s
        for _ in range(2000):
            ap += 1
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-16:
                break
        return s * math.exp(-x + a * math.log(x) - lg)
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 2000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-16:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - lg) * h


def _invert_increasing(f, p, lo, hi):
    """Solve f(x) = p for a monotonically increasing f on [lo, hi], expanding hi if needed."""
    while f(hi) < p:
        hi *= 2
        if hi > 1e12:
            break
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-14 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def gamma_ppf(p, a):
    return _invert_increasing(lambda x: gammp(a, x), p, 0.0, max(1.0, a * 2))


def chi2_cdf(x, k):
    return gammp(k / 2.0, x / 2.0)


def chi2_ppf(p, k):
    return gamma_ppf(p, k / 2.0) * 2.0


def ks_q(lam):
    """Kolmogorov distribution upper tail Q(lambda)."""
    if lam < 0.27:
        return 1.0
    s = 0.0
    for j in range(1, 101):
        t = math.exp(-2.0 * j * j * lam * lam)
        s += (-1) ** (j - 1) * t
        if t < 1e-16:
            break
    return min(1.0, max(0.0, 2.0 * s))


def _ks_lambda(d, n):
    sn = math.sqrt(n)
    return (sn + 0.12 + 0.11 / sn) * d  # Stephens' small-sample correction


def ks_pvalue(d, n):
    return ks_q(_ks_lambda(d, n))


def ks_critical(n, alpha):
    """Critical D for significance level alpha (same values as the usual Kolmogorov-Smirnov table)."""
    lo, hi = 1e-6, 1.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if ks_pvalue(mid, n) > alpha:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def gumbel_yn_sn(n):
    """Reduced mean Yn and reduced standard deviation Sn of Gumbel's table for sample size n."""
    y = [-math.log(-math.log(i / (n + 1.0))) for i in range(1, n + 1)]
    yn = sum(y) / n
    sn = math.sqrt(sum((v - yn) ** 2 for v in y) / n)
    return yn, sn


def reduced_variate(T):
    return -math.log(-math.log(1.0 - 1.0 / T))


# ----------------------------------------------------------------------------
# Distributions
# ----------------------------------------------------------------------------
def _p3_k(p, g):
    """Frequency factor K of the standardised Pearson III distribution (skew g) for non-exceedance p."""
    if abs(g) < 0.01:
        return norm_ppf(p)
    a = 4.0 / (g * g)
    if g > 0:
        return (gamma_ppf(p, a) - a) / math.sqrt(a)
    return -(gamma_ppf(1.0 - p, a) - a) / math.sqrt(a)


def _p3_cdf(z, g):
    if abs(g) < 0.01:
        return norm_cdf(z)
    a = 4.0 / (g * g)
    if g > 0:
        y = a + z * math.sqrt(a)
        return gammp(a, y) if y > 0 else 0.0
    y = a - z * math.sqrt(a)
    return 1.0 - gammp(a, y) if y > 0 else 1.0


class Dist:
    def __init__(self, name, nparams, cdf, ppf, kfac, params):
        self.name = name
        self.nparams = nparams
        self.cdf = cdf      # cdf(x)
        self.ppf = ppf      # ppf(p), p = non-exceedance probability
        self.kfac = kfac    # frequency factor K(p)
        self.params = params


def fit_distributions(x, gumbel_method="table"):
    """Fit Normal, Log Normal, Gumbel and Log Pearson III by the method of moments.

    gumbel_method: 'table' (Yn / Sn of Gumbel's table, Indonesian practice) or 'moments' (asymptotic).
    Log-based distributions are skipped when the data contain values <= 0.
    """
    n = len(x)
    m, s = mean(x), std(x)
    if s == 0:
        raise ValueError("All values are identical (standard deviation is zero).")
    out = []

    out.append(Dist(DIST_NORMAL, 2,
                    lambda v: norm_cdf((v - m) / s),
                    lambda p: m + norm_ppf(p) * s,
                    norm_ppf,
                    {"mean": m, "std": s}))

    if min(x) > 0:
        lx = [math.log10(v) for v in x]
        lm, ls = mean(lx), std(lx)
        lg = skewness(lx)
        if ls > 0:
            out.append(Dist(DIST_LOGNORMAL, 2,
                            lambda v: norm_cdf((math.log10(v) - lm) / ls) if v > 0 else 0.0,
                            lambda p: 10 ** (lm + norm_ppf(p) * ls),
                            norm_ppf,
                            {"log_mean": lm, "log_std": ls}))
            out.append(Dist(DIST_LP3, 3,
                            lambda v: _p3_cdf((math.log10(v) - lm) / ls, lg) if v > 0 else 0.0,
                            lambda p: 10 ** (lm + _p3_k(p, lg) * ls),
                            lambda p: _p3_k(p, lg),
                            {"log_mean": lm, "log_std": ls, "log_skew": lg}))

    if gumbel_method == "table":
        yn, sn = gumbel_yn_sn(n)
        kf = lambda p: (-math.log(-math.log(p)) - yn) / sn
        cdf = lambda v: math.exp(-math.exp(-(yn + sn * (v - m) / s)))
        params = {"Yn": yn, "Sn": sn}
    else:
        alpha = s * math.sqrt(6.0) / math.pi
        mu = m - EULER * alpha
        kf = lambda p: (-math.log(-math.log(p)) - EULER) * math.sqrt(6.0) / math.pi
        cdf = lambda v: math.exp(-math.exp(-(v - mu) / alpha))
        params = {"alpha": alpha, "mu": mu}
    out.insert(2 if len(out) >= 2 else len(out), Dist(DIST_GUMBEL, 2, cdf, lambda p: m + kf(p) * s, kf, params))
    return out


# ----------------------------------------------------------------------------
# Goodness of fit
# ----------------------------------------------------------------------------
def ks_test(x, dist, alpha, position="weibull"):
    """Smirnov-Kolmogorov test. position 'weibull': |m/(n+1) - F(x)| (common in Indonesian practice);
    'ecdf': standard statistic using both sides of the empirical CDF steps."""
    xs = sorted(x)
    n = len(xs)
    d = 0.0
    for i, v in enumerate(xs, 1):
        f = dist.cdf(v)
        if position == "ecdf":
            d = max(d, abs(i / n - f), abs((i - 1) / n - f))
        else:
            d = max(d, abs(i / (n + 1.0) - f))
    crit = ks_critical(n, alpha)
    p = ks_pvalue(d, n)
    return {"d": d, "crit": crit, "p": p, "ok": d < crit}


def chi2_classes(n, nparams, k_classes=0):
    if k_classes and k_classes > 0:
        return int(k_classes)
    k = int(math.ceil(1 + 3.322 * math.log10(n)))   # Sturges
    return max(k, nparams + 2)


def chi2_test(x, dist, alpha, k_classes=0):
    """Chi-square test with equal-probability classes."""
    n = len(x)
    k = chi2_classes(n, dist.nparams, k_classes)
    dof = k - 1 - dist.nparams
    bounds = [dist.ppf(j / float(k)) for j in range(1, k)]
    obs = [0] * k
    for v in x:
        obs[bisect.bisect_right(bounds, v)] += 1
    e = n / float(k)
    chi2 = sum((o - e) ** 2 / e for o in obs)
    if dof < 1:
        return {"chi2": chi2, "crit": None, "dof": dof, "p": None, "ok": None, "k": k, "obs": obs, "exp": e}
    crit = chi2_ppf(1.0 - alpha, dof)
    p = 1.0 - chi2_cdf(chi2, dof)
    return {"chi2": chi2, "crit": crit, "dof": dof, "p": p, "ok": chi2 < crit, "k": k, "obs": obs, "exp": e}


# ----------------------------------------------------------------------------
# Complete analysis
# ----------------------------------------------------------------------------
def analyse(x, return_periods, alpha=0.05, k_classes=0, ks_position="weibull", gumbel_method="table"):
    x = [float(v) for v in x]
    if len(x) < 5:
        raise ValueError("At least 5 values are needed (10 or more recommended).")
    stats = {
        "n": len(x), "mean": mean(x), "std": std(x), "cs": skewness(x), "ck": kurtosis(x),
        "min": min(x), "max": max(x),
    }
    stats["cv"] = stats["std"] / stats["mean"] if stats["mean"] != 0 else None
    if min(x) > 0:
        lx = [math.log10(v) for v in x]
        stats.update(log_mean=mean(lx), log_std=std(lx), log_cs=skewness(lx), log_ck=kurtosis(lx))
    else:
        stats.update(log_mean=None, log_std=None, log_cs=None, log_ck=None)

    dists = fit_distributions(x, gumbel_method)
    result = {"stats": stats, "dists": [], "data": list(x)}
    xs = sorted(x)
    n = len(xs)
    for d in dists:
        rows = []
        for T in return_periods:
            p = 1.0 - 1.0 / T
            rows.append({"T": T, "p_exceed": 1.0 / T, "k": d.kfac(p), "x": d.ppf(p)})
        result["dists"].append({
            "name": d.name, "nparams": d.nparams, "params": d.params, "quantiles": rows,
            "ks": ks_test(x, d, alpha, ks_position), "chi2": chi2_test(x, d, alpha, k_classes),
            "ppf": d.ppf,
            # root mean square error between observed values and the fitted quantiles (Weibull positions)
            "rmse": math.sqrt(sum((v - d.ppf(i / (n + 1.0))) ** 2 for i, v in enumerate(xs, 1)) / n),
        })
    return result
