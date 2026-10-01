"""
Risk engine - the part that actually keeps a trading account alive.

Everything here is borrowed from published professional practice:

  * Fixed-fractional / risk-per-trade sizing (Van Tharp, "Position Sizing":
    never risk more than ~0.5-2% of equity on one idea).
  * ATR-based stops ("N" stops - Turtle Trading; Chande's volatility stops).
  * Volatility targeting (bridge/MMF desks): scale exposure down when realized
    vol rises so each position contributes similar portfolio risk.
  * Correlation / sector-aware exposure caps (Solana memecoins are essentially
    one leveraged bet on SOL + risk appetite -> cap per-cluster and total
    invested %, plus BTC regime filter).
  * Kelly-lite fractional sizing using rolling trade win-rate & payoff.
  * Daily-loss circuit breaker + max-drawdown throttle (prop-firm style),
    because blowing up is the only unrecoverable error.
  * R-multiple management: partial take-profit at target, then move stop to
    breakeven, then trail with a Chandelier-style high-water-mark stop.

All numbers are env-overridable; defaults are conservative for a small account.
Pure stdlib, no external deps.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


# ---------------- config ----------------

@dataclass
class RiskConfig:
    # per-trade risk (stop distance based)
    risk_per_trade_pct: float = _f("RISK_PER_TRADE_PCT", 1.0)     # % of equity lost if stop hits
    atr_stop_mult: float = _f("ATR_STOP_MULT", 2.0)               # stop = entry - mult*ATR
    min_stop_pct: float = _f("MIN_STOP_PCT", 3.0)                 # never tighter than this
    max_stop_pct: float = _f("MAX_STOP_PCT", 15.0)                # never wider than this
    tp_atr_mult: float = _f("TP_ATR_MULT", 3.0)                   # first target (>=1.5R)
    hard_stop_pct: float = _f("HARD_STOP_PCT", 8.0)               # absolute disaster stop

    # r-multiple management
    partial_tp_pct: float = _f("PARTIAL_TP_PCT", 50.0)            # sell % at first target
    be_after_r: float = _f("BE_AFTER_R", 1.0)                     # move stop to BE after +1R
    chandelier_mult: float = _f("CHANDELIER_MULT", 3.0)           # trail: HHV(close,N) - m*ATR
    chandelier_window: int = int(_f("CHANDELIER_WINDOW", 22))

    # portfolio level
    max_position_pct: float = _f("MAX_POSITION_PCT", 6.0)         # notional cap per symbol
    max_total_exposure_pct: float = _f("MAX_TOTAL_EXPOSURE_PCT", 60.0)
    max_open_positions: int = int(_f("MAX_OPEN_POSITIONS", 8))
    cluster_max_pct: float = _f("CLUSTER_MAX_PCT", 35.0)          # per theme/chain bucket
    min_cash_reserve_pct: float = _f("MIN_CASH_RESERVE_PCT", 10.0)

    # volatility targeting
    vol_target_enabled: bool = _b("VOL_TARGET", True)
    target_vol_pct: float = _f("TARGET_VOL_PCT", 2.0)             # desired per-bar (hourly) vol
    vol_scale_min: float = _f("VOL_SCALE_MIN", 0.35)
    vol_scale_max: float = _f("VOL_SCALE_MAX", 1.30)

    # kelly-lite
    kelly_enabled: bool = _b("KELLY_ENABLED", True)
    kelly_fraction: float = _f("KELLY_FRACTION", 0.25)            # quarter-Kelly
    kelly_min_trades: int = int(_f("KELLY_MIN_TRADES", 12))

    # circuit breakers
    daily_loss_halt_pct: float = _f("DAILY_LOSS_HALT_PCT", 5.0)   # prop-style day kill switch
    max_drawdown_halt_pct: float = _f("MAX_DRAWDOWN_HALT_PCT", 20.0)
    cooldown_minutes: int = int(_f("COOLDOWN_MINUTES", 120))
    reentry_cooldown_minutes: int = int(_f("REENTRY_COOLDOWN_MIN", 45))
    halt_enabled: bool = _b("HALT_ENABLED", True)

    # market regime filter
    btc_regime_filter: bool = _b("BTC_REGIME_FILTER", True)
    btc_ema_period: int = int(_f("BTC_EMA_PERIOD", 50))
    reduce_when_btc_bearish: bool = _b("REDUCE_WHEN_BTC_BEARISH", True)
    bear_size_mult: float = _f("BEAR_SIZE_MULT", 0.5)

    # memecoin (degen) sleeve - caps for on-chain microcaps
    meme_enabled: bool = _b("MEME_RISK_ENABLED", True)
    meme_max_position_pct: float = _f("MEME_MAX_POSITION_PCT", 2.0)   # vs 6% normal
    meme_cluster_max_pct: float = _f("MEME_CLUSTER_MAX_PCT", 10.0)    # all memes combined
    meme_risk_per_trade_pct: float = _f("MEME_RISK_PER_TRADE_PCT", 0.5)
    meme_stop_pct: float = _f("MEME_STOP_PCT", 25.0)                  # wide but hard
    meme_take_profit_pct: float = _f("MEME_TAKE_PROFIT_PCT", 50.0)    # TP1 +50%
    meme_exit_on_volume_collapse: bool = _b("MEME_EXIT_VOL_COLLAPSE", True)


risk_config = RiskConfig()


# ---------------- clusters / correlation proxy ----------------

# Tokens in the same bucket are treated as correlated: one shared budget.
CLUSTERS: dict[str, str] = {
    "majors": {"btc", "xbt", "eth", "bnb", "xrp", "ada", "avax", "ltc", "ton"},
    "solana-beta": {"sol", "jup", "ray", "pyth", "jito", "mnde", "drift", "orca"},
    "solana-memes": {"bonk", "wif", "popcat", "slerf", "giga", "myro", "bera"},
    "evm-memes": {"pepe", "shib", "floki", "mog", "neiro", "turbo", "brevis", "brett", "pop"},
    "ai-defi": {"render", "rndr", "fetch", "ocean", "tao", "near", "arbitrum", "ai16z", "fartcoin",
                "virtual", "griffain", "clanker", "ens", "lna", "nosana", "zerebro"},
    "l2-infra": {"arb", "op", "matic", "pol", "base", "strk", "blast", "linea", "scroll"},
    "stables": {"usdc", "usdt", "dai", "tusd", "fdusd", "usde", "usd1"},
}

_SYMBOL_TO_CLUSTER: dict[str, str] = {}
for _name, _syms in CLUSTERS.items():
    for _s in _syms:
        _SYMBOL_TO_CLUSTER[_s] = _name


def cluster_of(symbol: str) -> str:
    s = (symbol or "").lower().strip()
    if s in _SYMBOL_TO_CLUSTER:
        return _SYMBOL_TO_CLUSTER[s]
    # heuristic: long strings are usually mints -> unknown but isolated
    if len(s) > 20:
        return f"mint:{s[:10]}"
    return "other"


def is_stable(symbol: str) -> bool:
    return cluster_of(symbol) == "stables"


# Memecoin sleeve: these buckets (plus unresolved long-ish tickers that the
# strategy tagged via memecoin.degen_score) get tighter notional caps.
_MEME_CLUSTERS = {"solana-memes", "evm-memes"}


def is_memecoin(symbol: str, force: bool = False) -> bool:
    """True when a symbol should be treated as degen/meme risk tier."""
    if force:
        return True
    s = (symbol or "").lower().strip()
    c = cluster_of(s)
    if c in _MEME_CLUSTERS:
        return True
    # AI-agent memes live in the ai-defi bucket but trade like memes
    if s in {"ai16z", "fartcoin", "virtual", "griffain", "clanker", "zerebro",
             "retardio", "moonbird", "pengu", "bera", "giga", "myro", "wif",
             "popcat", "slerf", "mog", "neiro", "turbo", "brevis", "brett",
             "pepe", "shib", "floki", "doge", "bonk"}:
        return True
    return False


def meme_stop_and_target(entry: float, cfg: RiskConfig = risk_config) -> tuple[float, float, float]:
    """Meme stops are percentage-based and wide (25%): DEX microcaps gap far
    too much for 2-ATR stops, but position size is cut so 0.5% equity max risk."""
    sp = cfg.meme_stop_pct
    stop = entry * (1.0 - sp / 100.0)
    tp = entry * (1.0 + cfg.meme_take_profit_pct / 100.0)
    return round(stop, 12), round(tp, 12), sp


# ---------------- core math ----------------

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def vol_scale(atr_pct_or_vol: Optional[float], cfg: RiskConfig = risk_config) -> float:
    """
    Volatility-targeting multiplier: hot tape -> smaller size.
    Input is per-bar volatility/ATR expressed in %.
    """
    if not cfg.vol_target_enabled or not atr_pct_or_vol or atr_pct_or_vol <= 0:
        return 1.0
    raw = cfg.target_vol_pct / atr_pct_or_vol
    return round(clamp(raw, cfg.vol_scale_min, cfg.vol_scale_max), 3)


def stop_and_target(
    entry: float,
    atr_value: Optional[float],
    side: str = "long",
    cfg: RiskConfig = risk_config,
) -> tuple[float, float, float]:
    """
    Returns (stop_price, take_profit_price, stop_pct).
    ATR-based (Turtle "N"), floored/capped by sane percentage bounds.
    """
    if entry <= 0:
        return entry, entry, cfg.min_stop_pct
    n = atr_value if (atr_value and atr_value > 0) else entry * (cfg.min_stop_pct / 100.0)
    dist = n * cfg.atr_stop_mult
    stop_pct = clamp(dist / entry * 100.0, cfg.min_stop_pct, cfg.max_stop_pct)
    dist = entry * stop_pct / 100.0
    if side == "short":
        stop = entry + dist
        tp = entry - dist * cfg.tp_atr_mult / cfg.atr_stop_mult
    else:
        stop = entry - dist
        tp = entry + dist * (cfg.tp_atr_mult / cfg.atr_stop_mult)
    # disaster guard
    hard = entry * (1 - cfg.hard_stop_pct / 100.0)
    stop = max(stop, hard) if side != "short" else stop
    return round(stop, 12), round(tp, 12), round(stop_pct, 3)


def kelly_size_multiplier(
    wins: int,
    losses: int,
    avg_win_pct: float,
    avg_loss_pct: float,
    cfg: RiskConfig = risk_config,
) -> tuple[float, dict]:
    """
    Fractional Kelly from realized closed-trade stats.
    avg_loss_pct is given as a positive number.
    Returns (multiplier 0..1.25, diagnostics).
    """
    trades = wins + losses
    info = {"trades": trades, "win_rate": 0.0, "payoff": 0.0, "kelly": 0.0}
    if trades < cfg.kelly_min_trades or not cfg.kelly_enabled:
        return 1.0, info
    p = wins / trades
    b = (avg_win_pct / avg_loss_pct) if avg_loss_pct > 0 else 1.0
    info.update(win_rate=round(p, 3), payoff=round(b, 2))
    if b <= 0:
        return 0.5, info
    k = (p * b - (1 - p)) / b          # full Kelly fraction of bankroll
    info["kelly"] = round(k, 3)
    if k <= 0:                          # negative edge -> shrink hard
        return 0.35, info
    mult = clamp(0.5 + k * cfg.kelly_fraction * 2.0, 0.4, 1.25)
    return round(mult, 3), info


@dataclass
class SizeResult:
    usd: float
    reasons: list[str] = field(default_factory=list)
    capped_by: str = ""


def position_size_usd(
    equity: float,
    cash: float,
    *,
    signal_score: float = 0.0,
    atr_pct: Optional[float] = None,
    stop_pct: Optional[float] = None,
    conviction: int = 1,
    leader_usd: float = 0.0,
    base_usd: Optional[float] = None,
    cluster_used_usd: float = 0.0,
    total_exposure_usd: float = 0.0,
    open_positions: int = 0,
    btc_bearish: bool = False,
    kelly_stats: Optional[tuple[int, int, float, float]] = None,
    is_meme: bool = False,
    meme_sleeve_used_usd: float = 0.0,
    cfg: RiskConfig = risk_config,
) -> SizeResult:
    """
    The single place that decides how big a buy is.

    Layers (multiplicative, then clamped):
      1. fixed-fractional risk budget  = equity * risk% / stop%
      2. signal-quality tilt           (score, conviction, leader notional)
      3. volatility target             (hot tape -> smaller)
      4. Kelly-lite                    (only if we have enough closed trades)
      5. hard caps                     (per-symbol, cluster, total exposure,
                                        open-position count, cash reserve)
      6. regime haircut                (BTC below its long EMA -> halve)
    """
    reasons: list[str] = []
    if equity <= 0 or cash <= 0:
        return SizeResult(0.0, ["no equity/cash"], "equity")

    # memecoin sleeve overrides: smaller notional cap + tighter risk budget
    if is_meme and cfg.meme_enabled:
        eq_frac = cfg.meme_max_position_pct / 100.0
        _risk_pct = cfg.meme_risk_per_trade_pct
        reasons.append(f"degen/meme tier: {cfg.meme_max_position_pct:.1f}% max position, "
                       f"{_risk_pct:.1f}% risk/trade")
    else:
        eq_frac = cfg.max_position_pct / 100.0
        _risk_pct = cfg.risk_per_trade_pct
    base = base_usd if (base_usd and base_usd > 0) else equity * eq_frac
    budget = base

    # 1) risk-per-trade driven budget (never bigger than the notional cap)
    sp = stop_pct
    if sp is None and atr_pct:
        sp = clamp(atr_pct * cfg.atr_stop_mult, cfg.min_stop_pct, cfg.max_stop_pct)
    if sp and sp > 0:
        risk_budget = equity * (_risk_pct / 100.0) / (sp / 100.0)
        budget = min(budget, max(risk_budget, 0.0))
        reasons.append(
            f"risk {_risk_pct:.1f}%/trade @ {sp:.1f}% stop -> ${risk_budget:,.0f} max"
        )

    # 2) signal quality
    tilt = 1.0
    s = abs(signal_score or 0.0)
    if s >= 60:
        tilt *= 1.25
        reasons.append(f"strong confluence ({signal_score:+.0f}) x1.25")
    elif s >= 40:
        tilt *= 1.10
        reasons.append(f"good confluence ({signal_score:+.0f}) x1.10")
    elif s >= 25:
        tilt *= 1.0
    else:
        tilt *= 0.75
        reasons.append(f"weak confluence ({signal_score:+.0f}) x0.75")
    if conviction > 1:
        cm = min(1.0 + 0.15 * (conviction - 1), 1.45)
        tilt *= cm
        reasons.append(f"{conviction} independent confirmations x{cm:.2f}")
    if leader_usd and leader_usd > 0:
        lt = min(1.0 + (leader_usd / 20_000.0) * 0.2, 1.3)
        tilt *= lt
        reasons.append(f"leader notional ${leader_usd:,.0f} x{lt:.2f}")
    budget *= tilt

    # 3) vol targeting
    vs = vol_scale(atr_pct, cfg)
    if vs != 1.0:
        budget *= vs
        reasons.append(f"vol-target x{vs:.2f} (ATR {atr_pct:.1f}% vs target {cfg.target_vol_pct:.1f}%)")

    # 4) Kelly-lite
    if kelly_stats:
        km, kd = kelly_size_multiplier(*kelly_stats, cfg=cfg)
        if kd["trades"] >= cfg.kelly_min_trades:
            budget *= km
            reasons.append(
                f"kelly-lite x{km:.2f} (WR {kd['win_rate']:.0%}, payoff {kd['payoff']:.1f}, "
                f"n={kd['trades']})"
            )

    # 5) hard caps
    capped_by = ""
    sym_cap = equity * eq_frac
    if budget > sym_cap:
        budget = sym_cap
        capped_by = "max_position_pct"
        reasons.append(f"capped to {eq_frac*100:.0f}% equity (${budget:,.0f})")

    # memecoin sleeve total cap (all degen positions combined)
    if is_meme and cfg.meme_enabled:
        sleeve_cap = equity * (cfg.meme_cluster_max_pct / 100.0) - meme_sleeve_used_usd
        if budget > sleeve_cap:
            budget = max(sleeve_cap, 0.0)
            capped_by = capped_by or "meme_sleeve_exposure"
            reasons.append(f"degen sleeve budget left ${max(sleeve_cap,0):,.0f}")

    cluster_cap = equity * (cfg.cluster_max_pct / 100.0) - cluster_used_usd
    if budget > cluster_cap:
        budget = max(cluster_cap, 0.0)
        capped_by = capped_by or "cluster_exposure"
        reasons.append(f"cluster budget left ${max(cluster_cap,0):,.0f}")

    exp_cap = equity * (cfg.max_total_exposure_pct / 100.0) - total_exposure_usd
    if budget > exp_cap:
        budget = max(exp_cap, 0.0)
        capped_by = capped_by or "total_exposure"
        reasons.append(f"portfolio exposure budget left ${max(exp_cap,0):,.0f}")

    if cfg.max_open_positions and open_positions >= cfg.max_open_positions:
        reasons.append(f"at max open positions ({cfg.max_open_positions}) -> no new buys")
        budget = 0.0
        capped_by = "max_open_positions"

    reserve = cash - equity * (cfg.min_cash_reserve_pct / 100.0)
    if budget > reserve:
        budget = max(reserve, 0.0)
        capped_by = capped_by or "cash_reserve"
        reasons.append(f"cash reserve {cfg.min_cash_reserve_pct:.0f}% limits to ${max(budget,0):,.0f}")

    # 6) regime haircut
    if btc_bearish and cfg.reduce_when_btc_bearish:
        budget *= cfg.bear_size_mult
        reasons.append(f"BTC bearish regime x{cfg.bear_size_mult:.2f}")

    return SizeResult(round(max(budget, 0.0), 4), reasons, capped_by)


# ---------------- exit logic (R-multiples) ----------------

@dataclass
class ExitDecision:
    action: str            # "hold" | "sell_all" | "sell_partial"
    amount: str            # "100%" | "50%" ...
    reason: str
    tag: str = ""


def evaluate_exit(
    *,
    entry: float,
    price: float,
    highest_seen: Optional[float],
    stop_price: Optional[float],
    take_profit: Optional[float],
    realized_r: Optional[float] = None,
    bars_held: Optional[float] = None,
    max_hold_hours: float = _f("MAX_HOLD_HOURS", 96.0),
    time_stop_gain_pct: float = _f("TIME_STOP_GAIN_PCT", 1.0),
    cfg: RiskConfig = risk_config,
) -> ExitDecision:
    """
    Deterministic, ordered exit rules (stops before targets):
      1. hard stop / structural stop hit            -> exit all
      2. reached first target                       -> bank half, keep runner
      3. chandelier trail from high-water mark      -> exit all
      4. moved to breakeven after +1R               -> stop = entry
      5. time stop (dead money)                     -> exit if flat
    """
    if entry <= 0 or price <= 0:
        return ExitDecision("hold", "0%", "no data")
    pnl_pct = (price - entry) / entry * 100.0
    stop_pct_ref = cfg.min_stop_pct
    if stop_price and stop_price < entry:
        stop_pct_ref = (entry - stop_price) / entry * 100.0
    r_mult = (pnl_pct / stop_pct_ref) if stop_pct_ref > 0 else 0.0

    # 1) stops
    if stop_price and price <= stop_price:
        if stop_price >= entry * 1.0005:
            return ExitDecision("sell_all", "100%",
                                f"trailing stop ${stop_price:.6g} hit at ${price:.6g} (+{pnl_pct:.1f}%)",
                                tag="trail_stop")
        return ExitDecision("sell_all", "100%",
                            f"STOP hit: ${price:.6g} <= ${stop_price:.6g} ({pnl_pct:+.1f}%, {r_mult:.2f}R)",
                            tag="stop")
    if pnl_pct <= -cfg.hard_stop_pct:
        return ExitDecision("sell_all", "100%",
                            f"HARD STOP {pnl_pct:.1f}% (limit -{cfg.hard_stop_pct:.0f}%)", tag="hard_stop")

    # 2) first target -> partial
    if take_profit and price >= take_profit:
        return ExitDecision("sell_partial", f"{cfg.partial_tp_pct:.0f}%",
                            f"TP1 ${take_profit:.6g} reached ({pnl_pct:+.1f}%, {r_mult:.1f}R) - "
                            f"bank {cfg.partial_tp_pct:.0f}%, trail rest",
                            tag="take_profit")

    # 3) chandelier trailing stop from high water mark
    if highest_seen and highest_seen > entry and cfg.chandelier_mult > 0:
        atr_val = (entry * stop_pct_ref / 100.0)
        trail = highest_seen - cfg.chandelier_mult * atr_val
        if price <= trail and pnl_pct > 0:
            return ExitDecision("sell_all", "100%",
                                f"chandelier exit: ${price:.6g} <= trail ${trail:.6g} "
                                f"(peak ${highest_seen:.6g}, +{pnl_pct:.1f}%)",
                                tag="chandelier")

    # 4) breakeven note (handled by caller updating stop_price)
    extra = ""
    if r_mult >= cfg.be_after_r and (realized_r or 0) < cfg.be_after_r:
        extra = f" | move stop to breakeven (+{cfg.be_after_r:.1f}R achieved)"

    # 5) time stop
    if bars_held is not None and max_hold_hours > 0 and bars_held >= max_hold_hours:
        if pnl_pct < time_stop_gain_pct:
            return ExitDecision("sell_all", "100%",
                                f"time stop: held {bars_held/3600:.1f}h for only {pnl_pct:+.1f}% "
                                f"(capital recycling)", tag="time_stop")

    return ExitDecision("hold", "0%",
                        f"{pnl_pct:+.1f}% ({r_mult:.2f}R) stop=${stop_price or 0:.6g} "
                        f"tp=${take_profit or 0:.6g}{extra}", tag="hold")


def breakeven_stop(entry: float, current_stop: Optional[float], r_mult: float,
                   cfg: RiskConfig = risk_config) -> Optional[float]:
    """After +be_after_r, ratchet the stop to just above entry."""
    if r_mult < cfg.be_after_r:
        return current_stop
    be = entry * 1.001
    if current_stop is None or current_stop < be:
        return be
    return current_stop


@dataclass
class TrailingConfig:
    """freqtrade `trailing_stop_*` semantics, mapped onto our ATR stops:
      activation_pct  -> trailing_stop_threshold (profit % before trail starts)
      distance_pct    -> trailing_stop_pulldown  (trail distance in price %)
      multiplier      -> optional ATR-based distance (overrides pct when set)
      start_at_entry  -> only ratchet once price > entry (freqtrade default)
    """
    enabled: bool = _b("TRAILING_STOP", True)
    activation_pct: float = _f("TRAILING_ACTIVATION_PCT", 4.0)
    distance_pct: float = _f("TRAILING_DISTANCE_PCT", 2.5)
    multiplier: float = _f("TRAILING_ATR_MULT", 0.0)   # 0 = use distance_pct
    start_at_entry: bool = _b("TRAILING_START_AT_ENTRY", True)


trailing_config = TrailingConfig()


def ratchet_trailing_stop(
    *,
    entry: float,
    price: float,
    highest_seen: Optional[float],
    current_stop: Optional[float],
    atr_value: Optional[float] = None,
    cfg: TrailingConfig = trailing_config,
) -> Optional[float]:
    """
    Port of freqtrade's `ft_stoploss_adjust` + `Trade.adjust_stop_loss` idea:
    once profit crosses the activation threshold, maintain a stop a fixed
    distance below the high-water mark and ONLY ever ratchet it upward
    (never loosen). Returns the new stop or the unchanged current one.
    """
    if not cfg.enabled or entry <= 0 or price <= 0:
        return current_stop
    hh = max(highest_seen or price, price)
    pnl_pct = (hh - entry) / entry * 100.0
    if pnl_pct < cfg.activation_pct:
        return current_stop
    if cfg.multiplier > 0 and atr_value and atr_value > 0:
        dist = cfg.multiplier * atr_value
    else:
        dist = hh * cfg.distance_pct / 100.0
    candidate = hh - dist
    if cfg.start_at_entry and candidate < entry:
        candidate = entry * 1.0005  # never trail below breakeven once activated
    if current_stop is None or candidate > current_stop:
        return round(candidate, 12)
    return current_stop


# ---------------- drawdown / breaker state ----------------

@dataclass
class BreakerState:
    peak_equity: float = 0.0
    day_date: str = ""
    day_start_equity: float = 0.0
    halted_until: float = 0.0
    last_exit_ts: float = 0.0
    notes: list[str] = field(default_factory=list)


def update_breakers(
    state: BreakerState,
    equity: float,
    cfg: RiskConfig = risk_config,
) -> BreakerState:
    """Call every tick with fresh mark-to-market equity."""
    import datetime as _dt
    today = _dt.date.today().isoformat()
    if state.day_date != today or state.day_start_equity <= 0:
        state.day_date = today
        state.day_start_equity = equity
        state.notes.append(f"new trading day {today}, start ${equity:,.2f}")
    if equity > state.peak_equity:
        state.peak_equity = equity

    if not cfg.halt_enabled:
        return state

    day_dd = ((state.day_start_equity - equity) / state.day_start_equity * 100.0
              if state.day_start_equity else 0.0)
    if day_dd >= cfg.daily_loss_halt_pct and state.halted_until < time.time():
        state.halted_until = time.time() + cfg.cooldown_minutes * 60.0
        state.notes.append(
            f"CIRCUIT BREAKER: -{day_dd:.1f}% on the day (limit {cfg.daily_loss_halt_pct:.0f}%) "
            f"-> new buys paused {cfg.cooldown_minutes}min"
        )

    peak_dd = ((state.peak_equity - equity) / state.peak_equity * 100.0
               if state.peak_equity else 0.0)
    if peak_dd >= cfg.max_drawdown_halt_pct and state.halted_until < time.time():
        state.halted_until = time.time() + cfg.cooldown_minutes * 60.0
        state.notes.append(
            f"DRAWDOWN HALT: {peak_dd:.1f}% off peak ${state.peak_equity:,.0f} "
            f"(limit {cfg.max_drawdown_halt_pct:.0f}%) -> de-risked, new buys paused"
        )
    return state


def can_open_new(state: BreakerState, now: Optional[float] = None) -> tuple[bool, str]:
    if not risk_config.halt_enabled:
        return True, "breakers disabled"
    now = now or time.time()
    if state.halted_until > now:
        mins = (state.halted_until - now) / 60.0
        return False, f"halted by circuit breaker ({mins:.0f} min remaining)"
    return True, "ok"


def reentry_allowed(state: BreakerState, symbol_last_exit_ts: float,
                    now: Optional[float] = None) -> bool:
    cfg = risk_config
    now = now or time.time()
    return (now - symbol_last_exit_ts) >= cfg.reentry_cooldown_minutes * 60.0


# ---------------- trade stats helper ----------------

def closed_trade_stats(trades: Sequence) -> tuple[int, int, float, float]:
    """
    Derive (wins, losses, avg_win_pct, avg_loss_pct) from a FIFO match of the
    trade log. `trades` items need .side/.symbol/.price/.usd_value attributes
    (portfolio.Trade). avg_* values are percentages, loss returned positive.
    """
    from collections import defaultdict, deque

    lots: dict[str, deque] = defaultdict(deque)
    wins = losses = 0
    gross_win = gross_loss = 0.0
    for t in trades:
        side = getattr(t, "side", "")
        sym = getattr(t, "symbol", "").lower()
        px = float(getattr(t, "price", 0) or 0)
        usd = float(getattr(t, "usd_value", 0) or 0)
        amt = float(getattr(t, "amount", 0) or 0)
        if side == "buy":
            cost = usd / amt if amt > 0 else px
            lots[sym].append([cost, amt])
        elif side == "sell":
            remaining = amt if amt > 0 else usd / px if px else 0
            q = lots[sym]
            while remaining > 1e-12 and q:
                lot = q[0]
                take = min(lot[1], remaining)
                if lot[0] > 0:
                    ret = (px - lot[0]) / lot[0] * 100.0
                    if ret >= 0:
                        wins += 1
                        gross_win += ret
                    else:
                        losses += 1
                        gross_loss += -ret
                lot[1] -= take
                remaining -= take
                if lot[1] <= 1e-12:
                    q.popleft()
    avg_win = (gross_win / wins) if wins else 0.0
    avg_loss = (gross_loss / losses) if losses else 0.0
    return wins, losses, avg_win, avg_loss
