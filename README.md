# gta6-twitch-forecast
[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Arkanjak/gta6-twitch-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Arkanjak/gta6-twitch-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Forecasts how many people watch Grand Theft Auto VI live on Twitch in each
hour of its release day (2026-11-19 UTC), from the first hour's viewer count.
`main.py` (Python 3.13, standard library only) reads one case file, fits a
forecaster live from that case's own comparable past launches, and writes five
quantiles (5%, 25%, 50%, 75%, 95%) for hours 2–24.

How it forecasts: each comparable launch's release day is expressed as
log-ratios to its own hour-1 viewers. Launches that do not behave like
GTA VI — simultaneous global unlocks (dead hours, then one spike),
early-access-contaminated launches, weak analogues, and the target's own
pre-release history — are dropped. Per hour, the log-ratio is regressed on
the launch's log hour-1 size, because big launches are already near
saturation in hour 1 and ramp little while small launches ramp several-fold
into the US evening. Quantiles are the fitted median times a residual spread,
with the hour-1 size clamped to the fitted range so an unprecedented launch
cannot extrapolate the ramp to nonsense. Anything that cannot be fitted falls
back to a persistence baseline instead of failing.

Backtest: on the four practice launches (ground truth from SullyGnome's public
hourly averages) this scores a mean pinball loss of about 0.18 on the log
scale, against about 0.39 for the house persistence baseline, and it beats
the baseline on every practice case. A leave-one-out test over the comparable
launches gives about 0.26 against the baseline's 0.40.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

The grader runs `python3 -P -s /bot/main.py` with no network; the program
reads every `*.in` file in the input directory and writes one `<id>.out` per
case. It is deterministic: the same input always produces the same bytes.

<sub>Entered on [make.fun](https://make.fun/?ref=ne0pfbnn&utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
