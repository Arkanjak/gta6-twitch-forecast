"""Forecast GTA VI launch-day Twitch viewers, hour by hour, from hour 1's print.

Method (fitted live from the case file's own comparables, no hard-coded curve):
for every past big launch in `comparables`, take its UTC release-day hourly
curve and express each hour as a log-ratio to that launch's hour-1 viewers.
Keep only the launches that behave like GTA VI will -- a rolling midnight
unlock on consoles -- and drop the rest:

* the target game's own pre-release history entry (not a launch day),
* launches whose `unlock` note tiers them early-access-contaminated or weak,
* launches whose hour-1 viewers are under 3% of their launch-day peak
  (a simultaneous global unlock: dead hours, then one sharp spike),
* launches missing hour 1 or most of the launch day.

Then, per forecast hour h, regress the log-ratio on the launch's log hour-1
size: big launches are already near saturation in hour 1 and ramp little,
small launches ramp several-fold into the US evening.  The regression gives
a median log-ratio and a residual spread; quantiles are
q = p1 * exp(median + z_tau * sigma), with sigma shrunk by 0.85 and the
effective log hour-1 clamped to the fitted range so an unprecedented GTA VI
hour 1 cannot extrapolate the ramp to nonsense.  The three hourly series
(median, slope, spread) are lightly smoothed across neighbouring hours.
Any hour that cannot be fitted, and any case that cannot be read at all,
falls back to the house persistence baseline instead of failing.

Standard library only, deterministic, no network, no clock, no randomness.
"""

import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]
BASE_SIGMA = 0.15          # persistence baseline's per-root-hour spread
# Shrink on the regression residual spread.  The tuning grid (leave-one-out
# over the kept comparables vs the four practice cases) is flat between 0.7
# and 0.9 -- LOO 0.261 at 0.9, practice 0.175 at 0.7 -- so 0.80 is the
# middle: it keeps most of the practice gain without over-tuning to four
# launches, and keeps the 90% band honest on the small-launch case.
SIGMA_SHRINK = 0.80        # shrink on the regression residual spread
MIN_KEPT = 8               # need this many usable comparables, else relax
MIN_DAY_HOURS = 20         # a usable comparable covers most of its launch day
H1_PEAK_FLOOR = 0.03       # hour 1 below this share of the day peak = simul. unlock
SLOPE_LO, SLOPE_HI = -1.2, 0.2
SIGMA_FLOOR = 0.12


def round2(x):
    """Round to 2 decimals, half up; whole numbers come back as ints."""
    scaled = x * 100.0
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100.0
    return int(value) if value.is_integer() else value


def parse_hour(text):
    """'2026-11-19T05:00:00Z' -> (days since epoch-ish, hour); None if unparsable.

    Only whole-hour UTC stamps are expected; anything else is skipped.
    """
    try:
        if len(text) < 13 or text[4] != "-" or text[7] != "-" or text[10] != "T":
            return None
        year = int(text[0:4])
        month = int(text[5:7])
        day = int(text[8:10])
        hour = int(text[11:13])
        if text[13:19] != ":00:00":
            return None
        # Days since 0001-01-01 via the proleptic Gregorian calendar.
        y = year - 1
        days = y * 365 + y // 4 - y // 100 + y // 400
        month_days = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
        leap = 1 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 0
        for m in range(month - 1):
            days += month_days[m] + (leap if m == 1 else 0)
        days += day - 1
        return (days, hour)
    except (ValueError, TypeError, IndexError):
        return None


def day_curve(comp):
    """{hour 1..24: viewers} for the comparable's UTC release day."""
    start = parse_hour(comp.get("day_start") or "")
    if start is None:
        return {}
    start_days, start_hour = start
    start_abs = start_days * 24 + start_hour
    curve = {}
    hourly = comp.get("hourly") or []
    for row in hourly:
        if not isinstance(row, dict):
            continue
        when = parse_hour(row.get("from") or "")
        if when is None:
            continue
        offset = (when[0] * 24 + when[1]) - start_abs
        if 0 <= offset < 24:
            viewers = row.get("viewers")
            if isinstance(viewers, (int, float)) and viewers > 0:
                curve[offset + 1] = float(viewers)
    return curve


def tier_of(comp):
    """'comparable' / 'early-access-contaminated' / 'weak' / None, from the note."""
    note = comp.get("unlock") or ""
    if "Tier:" not in note:
        return None
    tail = note.split("Tier:", 1)[1].strip()
    return tail.split()[0].strip(".,") if tail else None


def usable(comp, curve, target_name):
    """True when this comparable is a rolling-midnight launch like the target."""
    name = comp.get("name") or ""
    if target_name and name == target_name:
        return False
    if "Grand Theft Auto VI" in name and "before release" in name:
        return False
    tier = tier_of(comp)
    if tier is not None and tier != "comparable":
        return False
    if 1 not in curve or len(curve) < MIN_DAY_HOURS:
        return False
    peak = max(curve.values())
    if curve[1] < H1_PEAK_FLOOR * peak:
        return False
    return True


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def smooth3(series):
    """3-point moving average over hours 2..24; edges use what exists."""
    out = {}
    for hour in range(2, 25):
        near = [series[h] for h in (hour - 1, hour, hour + 1) if h in series]
        if near:
            out[hour] = sum(near) / len(near)
    return out


def fit_curves(curves):
    """Per-hour regression of log-ratio on centred log hour-1 size.

    Returns (median_log_ratio, slope, sigma, xbar, xmin, xmax) dicts, or
    None when too few usable curves were supplied.
    """
    if len(curves) < 4:
        return None
    log_h1 = [math.log(cv[1]) for cv in curves]
    xbar = sum(log_h1) / len(log_h1)
    med, slope, sig = {}, {}, {}
    for hour in range(2, 25):
        points = []
        for cv in curves:
            if hour in cv:
                points.append((math.log(cv[1]) - xbar, math.log(cv[hour] / cv[1])))
        if len(points) < 8:
            continue
        n = len(points)
        mean_x = sum(p[0] for p in points) / n
        mean_y = sum(p[1] for p in points) / n
        sxx = sum((p[0] - mean_x) ** 2 for p in points)
        sxy = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points)
        b = sxy / sxx if sxx > 1e-9 else 0.0
        b = max(SLOPE_LO, min(SLOPE_HI, b))
        a = mean_y - b * mean_x
        resid = [p[1] - (a + b * p[0]) for p in points]
        var = sum(r * r for r in resid) / max(n - 2, 1)
        med[hour] = a
        slope[hour] = b
        sig[hour] = max(math.sqrt(var), SIGMA_FLOOR) * SIGMA_SHRINK
    if len(med) < 12:
        return None
    med = smooth3(med)
    slope = smooth3(slope)
    sig = smooth3(sig)
    # A launch-day curve is smooth, so an hour the fit missed (too few
    # launches reported it) is interpolated from its fitted neighbours
    # rather than dropped to the persistence baseline for that one hour.
    for series in (med, slope, sig):
        for hour in range(2, 25):
            if hour in series:
                continue
            below = [h for h in range(2, hour) if h in series]
            above = [h for h in range(hour + 1, 25) if h in series]
            if below and above:
                lo, hi = below[-1], above[0]
                w = (hour - lo) / (hi - lo)
                series[hour] = series[lo] * (1.0 - w) + series[hi] * w
            elif below:
                series[hour] = series[below[-1]]
            elif above:
                series[hour] = series[above[0]]
    return {
        "med": med,
        "slope": slope,
        "sig": sig,
        "xbar": xbar,
        "xmin": min(log_h1),
        "xmax": max(log_h1),
    }


def baseline_row(p1, hour):
    spread = BASE_SIGMA * math.sqrt(hour - 1)
    return [round2(p1 * math.exp(z * spread)) for z in Z]


def forecast_case(case):
    known = case.get("known") or []
    first = None
    for row in known:
        if isinstance(row, dict) and row.get("hour") == 1:
            first = row
            break
    if first is None and known:
        first = known[0]
    viewers = first.get("viewers") if isinstance(first, dict) else None
    p1 = float(viewers) if isinstance(viewers, (int, float)) and viewers > 0 else 1.0
    p1 = max(1.0, p1)

    hours = []
    forecast = case.get("forecast") or []
    for row in forecast:
        if isinstance(row, dict) and isinstance(row.get("hour"), int):
            hours.append(row["hour"])
    if not hours:
        hours = list(range(2, 25))
    hours = sorted(set(h for h in hours if 2 <= h <= 24)) or list(range(2, 25))

    target_name = ""
    game = case.get("game") or {}
    if isinstance(game, dict):
        target_name = game.get("name") or ""

    fit = None
    comparables = case.get("comparables") or []
    if isinstance(comparables, list):
        curves = []
        for comp in comparables:
            if not isinstance(comp, dict):
                continue
            curve = day_curve(comp)
            if usable(comp, curve, target_name):
                curves.append(curve)
        if len(curves) < MIN_KEPT:
            # Relax: any launch with a real hour 1 and a full-ish day.
            relaxed = []
            for comp in comparables:
                if not isinstance(comp, dict):
                    continue
                curve = day_curve(comp)
                if 1 in curve and len(curve) >= MIN_DAY_HOURS:
                    relaxed.append(curve)
            if len(relaxed) > len(curves):
                curves = relaxed
        fit = fit_curves(curves)

    rows = []
    log_p1 = math.log(p1)
    for hour in hours:
        if fit is not None and hour in fit["med"]:
            x = max(fit["xmin"], min(fit["xmax"], log_p1)) - fit["xbar"]
            centre = math.log(p1) + fit["med"][hour] + fit["slope"][hour] * x
            sigma = fit["sig"][hour]
            qs = []
            for z in Z:
                value = math.exp(centre + z * sigma)
                value = min(max(value, 0.01), 1e9)
                qs.append(round2(value))
        else:
            qs = baseline_row(p1, hour)
        for i in range(1, 5):
            if qs[i] < qs[i - 1]:
                qs[i] = qs[i - 1]
        qs = [min(max(q, 0.01), 1e9) for q in qs]
        rows.append({"hour": hour, "q": qs})
    return {"hours": rows}


def safe_forecast(case, stem):
    """Never raise: a broken case still gets a valid baseline-shaped output."""
    try:
        return forecast_case(case)
    except Exception:
        return {"hours": [{"hour": h, "q": baseline_row(1.0, h)} for h in range(2, 25)]}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(case, dict):
                raise ValueError("case is not an object")
            forecast = safe_forecast(case, path.stem)
        except Exception:
            forecast = {"hours": [{"hour": h, "q": baseline_row(1.0, h)}
                                  for h in range(2, 25)]}
        text = json.dumps(forecast, separators=(",", ":")) + "\n"
        (OUTPUT_DIR / (path.stem + ".out")).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
