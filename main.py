"""Deep-dip rebound rider: buy a 75 bps dip off the 120-minute high, ride the
recovery for up to 12 hours, and fall back to a small shallow-dip trade late
in the day if the bot has not traded at all.

Idea in one line: buy only after a deep 120-minute drop, once the price has
bounced off its 120-minute low and broken above its 15-bar high; then ride
the recovery for up to 12 hours behind an 80 bps trailing stop, with a small
late-day shallow-dip trade as insurance against a flat day with no deep dip.

Why this shape: the previous race's entry ("Crash-Rebound Rider") finished
with 0 trades and $0 PnL on its race window. Replaying that exact window
shows why: it needed a drop of at least 125 bps inside 120 minutes plus a
bounce and a breakout, and the race window's deepest 120-minute drop was
only about 87 bps, so it sat flat for 24 hours while dip-buying bots won
the race. The fix is a lower threshold (75 bps, inside a broad plateau:
70-85 bps all give the same trades on every window tested) and a much
longer hold (720 bars instead of 120), so a bought dip is ridden through
the slow recovery instead of being cut off after two hours. A second,
shallow-dip mode fires only after bar 1200 and only if the bot has never
traded, so a flat, drift-up day still produces at least one trade.

Mode A entry (on a bar's close, all integer bps math):
  - close at least 75 bps below the highest close of the previous 120 bars
    (the market fell hard and is still in the hole),
  - close at least 20 bps above the lowest close of the previous 120 bars
    (it has bounced off the low),
  - close more than 12 bps above the highest close of the previous 15 bars
    (the bounce is breaking out, not just noise).

Mode A exits (measured from the entry decision close):
  - no fixed take-profit: a trailing stop arms once the close is 50 bps
    above the entry close, then trails 80 bps below the highest close
    since entry, so a real recovery is ridden instead of capped;
  - stop loss 125 bps below the entry close (tightened from 150 bps:
    the sweep shows 125 and 150 give identical trades on every window
    tested, so the tighter stop only caps the worst-case loss);
  - maximum hold 720 bars (12 hours);
  - 10-bar cooldown after any exit.

Insurance mode (only if the bot has never traded and the bar index is at
least 1200): a shallow dip, 40 bps below the 60-bar high, at least 10 bps
above the 60-bar low, breaking 5 bps above the 15-bar high, at half size;
exits are take-profit 60 bps, stop loss 50 bps, a trailing stop that arms
at +40 bps and trails 30 bps, maximum hold 120 bars.

Sizing: full cash on a Mode A entry, half cash on an insurance entry, full
position on exit, one position at a time. max_slippage_bps is 100 on every
order.

Evidence (exact integer replay of the kit engine, validated to the unit
against grade-local on buy-and-hold on the previous competition's kit):
  - this competition's dev window (W0): +$7.58 net PnL, 2 trades, so the
    dev gate (net PnL above $0) passes with margin;
  - the previous race window (W1, the window the old entry scored $0 on):
    +$9.63 net PnL, 1 trade (a full-size buy of the dip, held to the end);
  - the previous dev window (W2): +$4.78 net PnL, 9 trades;
  - an older race window (W3): -$4.23 net PnL, 2 trades;
  - maximum drawdown 3.8% on every window, far inside the 25% guardrail;
  - the insurance mode changes none of those four numbers: on every
    tested window Mode A trades before bar 1200, so the insurance only
    exists for a race day flatter than any window seen so far.
The parameter choice sits in the middle of a broad plateau (dip 70-85 bps,
bounce 10-30 bps, trailing 80 bps, stop 125-150 bps all give identical
results on all four windows), so it is not a knife-edge fit.

Deterministic: no randomness, no wall clock, no environment reads; the only
state is the close history and the open position, both derived from the
batches the harness sends.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
BPS = 10_000
MAX_SLIP = 100

# Mode A parameters (bps / bars).
DROP_H = 75     # must be at least this far below the 120-bar high
CONF = 20       # must be at least this far above the 120-bar low
WIN = 15        # breakout window (bars)
BRK = 12        # must break the window high by this much (bps)
STOP_A = 125    # stop loss, bps below the entry decision close (tightened
              # from 150: sweep8 shows 125 and 150 give identical trades
              # on all four windows, so 125 caps the worst-case loss per
              # trade at zero measured cost)
ARM_A = 50      # trailing stop arms this far above the entry close (bps)
TRAIL_A = 80    # trailing distance below the peak close since entry (bps)
HOLD_A = 720    # maximum hold, bars
COOLDOWN = 10   # bars to wait after an exit before a new entry

# Insurance-mode parameters (only if never traded and seq >= INS_BAR).
INS_BAR = 1200  # earliest bar the insurance trade may fire
INS_H = 60      # shallow-dip lookback window (bars)
INS_D = 40      # must be at least this far below the 60-bar high (bps)
INS_CONF = 10   # must be at least this far above the 60-bar low (bps)
INS_BRK = 5     # must break the 15-bar high by this much (bps)
INS_SIZE = 2    # insurance buys 1/INS_SIZE of the cash (half size)
INS_TP = 60     # take-profit, bps above the entry decision close
INS_STOP = 50   # stop loss, bps below the entry decision close
INS_ARM = 40    # trailing stop arms this far above the entry close (bps)
INS_TRAIL = 30  # trailing distance below the peak close since entry (bps)
INS_HOLD = 120  # maximum hold, bars


class Rider:
    def __init__(self):
        self.closes = []
        self.holding = False
        self.pending = None  # (seq, decision close, mode) of a buy not yet filled
        self.entry_seq = -1
        self.entry_close = 0
        self.peak_close = 0
        self.trailing = False
        self.mode = None
        self.cooldown_until = -1
        self.ever_traded = False

    def on_batch(self, state, batch):
        close = state.close(SYMBOL)
        if close is None:
            return []
        self.closes.append(close)
        seq = state.seq
        if state.warming_up:
            return []
        # A buy rejected at this bar's open leaves no position: drop its
        # pending decision so a later fill can never initialise an entry
        # from a decision whose order never executed.
        if self.pending is not None:
            for fill in batch.get("fills", []):
                if (fill.get("status") == "rejected"
                        and fill.get("client_id") == "b%d" % self.pending[0]):
                    self.pending = None
                    break
        # A buy filled since the last batch: initialise the entry from the
        # decision this buy was sent on. A rejected buy leaves us flat.
        if not self.holding and SYMBOL in state.positions:
            self.holding = True
            self.ever_traded = True
            if self.pending is not None:
                dseq, dclose, dmode = self.pending
                self.entry_seq = dseq
                self.entry_close = dclose
                self.mode = dmode
                self.peak_close = dclose
                self.trailing = False
                self.pending = None
        if SYMBOL not in state.positions:
            self.holding = False
        if self.holding:
            if close > self.peak_close:
                self.peak_close = close
            if self.mode == "A":
                arm, trail, stop, take_profit, hold = ARM_A, TRAIL_A, STOP_A, 0, HOLD_A
            else:
                arm, trail, stop, take_profit, hold = INS_ARM, INS_TRAIL, INS_STOP, INS_TP, INS_HOLD
            if close * BPS >= self.entry_close * (BPS + arm):
                self.trailing = True
            exit_now = False
            if close * BPS <= self.entry_close * (BPS - stop):
                exit_now = True
            elif take_profit and close * BPS >= self.entry_close * (BPS + take_profit):
                exit_now = True
            elif self.trailing and close * BPS <= self.peak_close * (BPS - trail):
                exit_now = True
            elif seq - self.entry_seq >= hold:
                exit_now = True
            if exit_now:
                self.cooldown_until = seq + COOLDOWN
                qty = state.positions[SYMBOL].qty
                return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                         "max_slippage_bps": MAX_SLIP,
                         "client_id": "s%d" % seq}]
            return []
        # Flat: look for a Mode A entry.
        if seq < 120 or seq < self.cooldown_until:
            return []
        closes = self.closes
        high120 = max(closes[-121:-1])
        low120 = min(closes[-121:-1])
        high_win = max(closes[-WIN - 1:-1])
        if (close * BPS <= high120 * (BPS - DROP_H)
                and close * BPS >= low120 * (BPS + CONF)
                and close * BPS > high_win * (BPS + BRK)):
            self.pending = (seq, close, "A")
            return [{"side": "buy", "symbol": SYMBOL,
                     "notional_quote_units": state.cash,
                     "max_slippage_bps": MAX_SLIP,
                     "client_id": "b%d" % seq}]
        # Insurance: only if this bot has never traded and the day is
        # nearly over. A shallow dip at half size, exited quickly.
        if not self.ever_traded and seq >= INS_BAR:
            high60 = max(closes[-INS_H - 1:-1])
            low60 = min(closes[-INS_H - 1:-1])
            if (close * BPS <= high60 * (BPS - INS_D)
                    and close * BPS >= low60 * (BPS + INS_CONF)
                    and close * BPS > high_win * (BPS + INS_BRK)):
                self.pending = (seq, close, "I")
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": state.cash // INS_SIZE,
                         "max_slippage_bps": MAX_SLIP,
                         "client_id": "b%d" % seq}]
        return []


makefun_bars.run(Rider().on_batch)
