# Deep-dip rebound rider: buy a 75 bps dip, ride the recovery up to 12 hours

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/whyuardi/makefun-btc-race-20261118-1444/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/whyuardi/makefun-btc-race-20261118-1444/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

A BTC trading bot for the make.fun 24-hour race. It trades Coinbase Wrapped
BTC (cbBTC) against USDC on 1-minute on-chain prices, long only, starting
with $1,000 of paper money.

## Idea

Buy only after a deep 120-minute drop, once the price has bounced off its
120-minute low and broken above its 15-bar high; then ride the recovery for
up to 12 hours behind an 80 bps trailing stop, with a small late-day
shallow-dip trade as insurance against a flat day with no deep dip.

## Why this shape

The previous race's entry ("Crash-Rebound Rider") finished with 0 trades and
$0 PnL on its race window. Replaying that exact window shows why: it needed
a drop of at least 125 bps inside 120 minutes plus a bounce and a breakout,
and the race window's deepest 120-minute drop was only about 87 bps, so it
sat flat for 24 hours while dip-buying bots won the race. The fix is a lower
threshold (75 bps, inside a broad plateau: 70-85 bps all give the same trades
on every window tested) and a much longer hold (720 bars instead of 120), so
a bought dip is ridden through the slow recovery instead of being cut off
after two hours. A second, shallow-dip mode fires only after bar 1200 and
only if the bot has never traded, so a flat, drift-up day still produces at
least one trade.

## Rules (all integer bps math, on a bar's close)

Mode A entry:

- close at least 75 bps below the highest close of the previous 120 bars,
- close at least 20 bps above the lowest close of the previous 120 bars,
- close more than 12 bps above the highest close of the previous 15 bars.

Mode A exits (from the entry decision close):

- no fixed take-profit: a trailing stop arms at +50 bps, then trails 80 bps
  below the highest close since entry;
- stop loss 150 bps below the entry close;
- maximum hold 720 bars (12 hours);
- 10-bar cooldown after any exit.

Insurance mode (only if the bot has never traded and the bar index is at
least 1200): a shallow dip, 40 bps below the 60-bar high, at least 10 bps
above the 60-bar low, breaking 5 bps above the 15-bar high, at half size;
exits are take-profit 60 bps, stop loss 50 bps, a trailing stop that arms
at +40 bps and trails 30 bps, maximum hold 120 bars.

Sizing: full cash on a Mode A entry, half cash on an insurance entry, full
position on exit, one position at a time. `max_slippage_bps` is 100 on every
order.

## Evidence (exact integer replay of the kit engine)

The replay harness is an exact port of the kit's fill model, validated to
the unit against the server's own grades on the previous competition
(buy-and-hold, and the previous entry's +$24.68 dev / $0.00 race, both
reproduced exactly). The submitted `main.py` itself was run through that
harness on all four windows and through the real stdio protocol twice
(byte-identical replies, deterministic):

| window | net PnL | trades | max drawdown |
| --- | --- | --- | --- |
| this competition's dev window | +$7.58 | 2 | 1.0% |
| previous race window (the one the old entry scored $0 on) | +$9.63 | 1 | 1.3% |
| previous dev window | +$4.78 | 9 | 3.8% |
| an older race window | -$4.23 | 2 | 1.0% |

The dev gate (net PnL above $0) passes with margin, and the maximum
drawdown stays far inside the 25% guardrail on every window. The insurance
mode changes none of those four numbers: on every tested window Mode A
trades before bar 1200, so the insurance only exists for a race day flatter
than any window seen so far. The parameter choice sits in the middle of a
broad plateau (dip 70-85 bps, bounce 10-30 bps, trailing 80 bps, stop
125-150 bps all give identical results on all four windows), so it is not a
knife-edge fit.

## Files

- `main.py` — the bot (Python 3.13, standard library only). Written fresh
  in this session; the entry logic is a single `Rider` class driven by the
  kit's `makefun_bars` shim.
- `vendor/makefun_bars.py` — byte-for-byte copy of the kit's Python shim
  (`sdk/makefun_bars.py`), kept under `vendor/` as the kit instructs.

## Grade locally

```sh
node cli/makefun.mjs grade-local --kit <kit-dir> --bot . --driver docker
```

(Docker is required for a Python bot; the process driver runs Node bots
only. Without Docker, the same result can be reproduced with the replay
harness in `research/` — see the evidence table above.)
