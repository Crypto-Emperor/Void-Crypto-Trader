"""
Confluence strategy - freqtrade-style signal engine built on real candles.

Design borrowed from freqtrade (https://github.com/freqtrade/freqtrade):
  * strategies populate an indicator dataframe, then set boolean entry/exit
    columns in a vectorized `populate_*` pass (here: `analyze()`),
  * `confirm_trade_entry`-style guards gate every buy (our protections +
    liquidity/volatility pair filters from pairlist.py),
  * custom stoploss = ratcheting trailing stop (risk.ratchet_trailing_stop),
  * informative multi-timeframe: 4h trend must agree with the 1h trigger
    (freqtrade's `informative_pair` merge pattern).

Trader research implemented here:
  * Trend following (Turtle/Donchian breakout + EMA ribbon + ADX>=25 filter)
  * Larry Connors RSI(2) pullback buys inside an above-EMA200 tape
  * MACD histogram momentum confirmation
  * Volume spike confirmation (smart-money flow proxy)
  * Regime switching: trend rules only in trending tape, mean-rev only in chop
All entries are sized by risk.position_size_usd (fixed-fractional + vol target
+ Kelly-lite + cluster caps) and exits run through risk.evaluate_exit
(stops -> TP1 partial -> chandelier trail -> breakeven -> time stop).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional

from config import settings
from portfolio import Portfolio
from strategies.base import Strategy, Signal
from market_data import get_candles
from indicators import snapshot, confluence_score, regime
import risk
import pairlist as pl
from protections import get_protections, trades_from_portfolio

# optional DEX/memecoin layer (GeckoTerminal + DexScreener); bot runs fine without it
try:
    import memecoin as mm
    MEME_LAYER = True
except Exception:  # pragma: no cover
    mm = None
    MEME_LAYER = False


def _meme_mode() -> str:
    """off | auto (score memes when they appear in universe) | degen (scan trending)."""
    return os.getenv("MEME_MODE", "auto").lower()


@dataclass
class Analysis:
    symbol: str
    price: float
    score_1h: float = 0.0
    score_4h: float = 0.0
    bulls: list[str] = field(default_factory=list)
    bears: list[str] = field(default_factory=list)
    atr_pct: Optional[float] = None
    atr_value: Optional[float] = None
    stop_pct: Optional[float] = None
    volume_ratio: Optional[float] = None
    donchian_break_up: bool = False
    ema9_gt_ema21: bool = False
    adx: Optional[float] = None
    rsi2: Optional[float] = None
    rsi14: Optional[float] = None
    macd_bull: bool = False
    structure_up: bool = False
    above_ema_slow_4h: bool = False
    regime: str = "neutral"
    enough_history: bool = False
    liquid: bool = True
    reasons: list[str] = field(default_factory=list)


def analyze(symbol: str) -> Optional[Analysis]:
    """Vectorized 'populate_indicators' equivalent across 1h + 4h timeframes."""
    snap = snapshot(symbol, "1h", 250)
    if snap is None or not snap.enough_history:
        return None
    score, bulls, bears = confluence_score(snap)

    a = Analysis(
        symbol=symbol.lower(),
        price=snap.price,
        score_1h=score,
        bulls=bulls,
        bears=bears,
        atr_pct=snap.atr_pct,
        volume_ratio=snap.volume_ratio,
        donchian_break_up=snap.donchian_break_up,
        ema9_gt_ema21=bool(snap.ema_fast and snap.ema_slow and snap.ema_fast > snap.ema_slow),
        adx=snap.adx,
        rsi2=snap.rsi_fast,
        rsi14=snap.rsi,
        macd_bull=bool(snap.macd_hist is not None and snap.price
                       and snap.macd_hist / snap.price * 100.0 > 0.02),
        structure_up=(snap.structure == "uptrend"),
        regime=regime(snap),
        enough_history=True,
    )

    cs = get_candles(symbol, "1h", 60)
    if cs:
        from indicators import atr as _atr
        vals = _atr(cs, 14)
        a.atr_value = vals[-1] if vals and vals[-1] else None

    # informative 4h timeframe (MTF trend agreement)
    snap4 = snapshot(symbol, "4h", 200)
    if snap4 is not None and snap4.enough_history:
        s4, _b4, _r4 = confluence_score(snap4)
        a.score_4h = s4
        a.above_ema_slow_4h = bool(snap4.ema_slow and snap4.price and
                                   snap4.price > snap4.ema_slow)

    # liquidity gate (freqtrade VolumePairList lesson: no liquidity, no trade)
    try:
        a.liquid = pl.best_pair(symbol) is not None
    except Exception:
        a.liquid = True

    return a


class ConfluenceStrategy(Strategy):
    """Multi-factor, multi-timeframe confluence engine with full risk gating."""

    name = "confluence"

    def __init__(self, account_id: str = "1"):
        self.account_id = account_id

    # ---- universe -------------------------------------------------------
    def _universe(self, pf: Portfolio) -> list[str]:
        seed = [s for s in settings.symbol_list]
        extra = list(pf.positions.keys())
        mode = os.getenv("PAIRLIST_MODE", "static").lower()
        if mode == "volume":
            try:
                top = pl.VolumePairList(
                    number_assets=int(os.getenv("PAIRLIST_TOP_N", "30"))).apply([])
                keep = os.getenv("PAIRLIST_KEEP_SEED", "true").lower() in ("1", "true", "yes")
                uni = (top + extra) if keep else top
                seen, out = set(), []
                for s in uni:
                    if s not in seen:
                        seen.add(s)
                        out.append(s)
                return out
            except Exception:
                pass
        # degen mode: pull today's trending DEX pools into the scan universe
        if MEME_LAYER and _meme_mode() in ("degen", "trending"):
            try:
                net = os.getenv("MEME_DEFAULT_NETWORK", "solana")
                hot = mm.hot_memecoins(network=net,
                                       limit=int(os.getenv("MEME_TRENDING_TOP_N", "10")))
                memes = [d.symbol for d in hot if not d.vetoes]
                out, seen = [], set()
                for s in memes + seed + extra:
                    if s and s not in seen:
                        seen.add(s)
                        out.append(s)
                return out
            except Exception:
                pass
        out, seen = [], set()
        for s in seed + extra:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    # ---- entry logic (populate_entry_trend equivalent) ------------------
    def _entry_signals(self, a: Analysis) -> tuple[bool, int, list[str]]:
        """Returns (enter, conviction_count, why_list)."""
        conv: list[str] = []

        # hard vetoes
        if not a.enough_history:
            return False, 0, ["insufficient history"]
        if not a.liquid:
            return False, 0, ["not listed on deep USDT market (liquidity veto)"]
        if a.score_1h < 20:
            return False, 0, [f"1h score {a.score_1h:+.0f} < 20"]

        # MTF agreement: 4h must not be strongly against us
        if a.score_4h <= -20:
            return False, 0, [f"4h trend disagrees ({a.score_4h:+.0f})"]

        # rule A: Donchian breakout trend entry (Turtles)
        if a.donchian_break_up and a.ema9_gt_ema21:
            conv.append("donchian breakout + EMA stack")
        # rule B: strong positive confluence with ADX trend strength
        if a.score_1h >= 45 and (a.adx or 0) >= 22:
            conv.append(f"confluence {a.score_1h:+.0f}, ADX {a.adx:.0f}")
        # rule C: MACD momentum + uptrend structure
        if a.macd_bull and a.structure_up:
            conv.append("MACD momentum + higher highs/lows")
        # rule D: volume confirmation on any of the above
        if a.volume_ratio and a.volume_ratio >= 1.5 and conv:
            conv.append(f"volume {a.volume_ratio:.1f}x")
        # rule E: Connors RSI(2) pullback in a healthy uptrend (mean-reversion
        # only allowed when the tape is trending up AND 4h agrees)
        if (a.rsi2 is not None and a.rsi2 < 10 and a.above_ema_slow_4h
                and a.ema9_gt_ema21 and a.regime != "trending-down"):
            conv.append(f"RSI(2)={a.rsi2:.0f} pullback in uptrend")

        min_conv = int(os.getenv("CONFLUENCE_MIN_CONFIRMATIONS", "2"))
        if len(conv) < min_conv:
            return False, len(conv), conv or [f"only {len(conv)} confirmation(s)"]
        # require at least one *trend* pillar (A/B/C/E), D alone never counts
        if conv and all("volume" in c for c in conv):
            return False, len(conv), ["volume without trend pillar"]
        return True, len(conv), conv

    # ---- main ------------------------------------------------------------
    def generate(self, portfolio: Portfolio, prices: dict[str, float]) -> list[Signal]:
        signals: list[Signal] = []
        prot = get_protections()
        records = trades_from_portfolio(portfolio)

        equity = portfolio.total_equity(prices) or portfolio.cash
        total_exposure = sum(p.market_value(prices.get(s, p.avg_entry))
                             for s, p in portfolio.positions.items())

        # ---------- manage open positions first (exits + trailing) --------
        for sym, pos in list(portfolio.positions.items()):
            px = prices.get(sym)
            if not px:
                continue
            if pos.highest_seen is None or px > pos.highest_seen:
                pos.highest_seen = px

            a = analyze(sym)
            atr_val = a.atr_value if a else None
            stop_pct_ref = ((pos.avg_entry - pos.stop_price) / pos.avg_entry * 100.0
                            if pos.stop_price and pos.stop_price < pos.avg_entry
                            else risk.risk_config.min_stop_pct)

            # ratcheting trailing stop (freqtrade ft_stoploss_adjust port)
            new_stop = risk.ratchet_trailing_stop(
                entry=pos.avg_entry, price=px, highest_seen=pos.highest_seen,
                current_stop=pos.stop_price, atr_value=atr_val)
            if new_stop and (pos.stop_price is None or new_stop > pos.stop_price):
                pos.stop_price = new_stop

            bars_held = pos.age_hours() * 3600.0
            dec = risk.evaluate_exit(
                entry=pos.avg_entry, price=px,
                highest_seen=pos.highest_seen,
                stop_price=pos.stop_price,
                take_profit=None if pos.tp_done else pos.take_profit,
                realized_r=None, bars_held=bars_held,
            )
            # breakeven ratchet note -> apply immediately
            r_mult = ((px - pos.avg_entry) / pos.avg_entry * 100.0) / max(stop_pct_ref, 0.1)
            be = risk.breakeven_stop(pos.avg_entry, pos.stop_price, r_mult)
            if be and (pos.stop_price is None or be > pos.stop_price):
                pos.stop_price = be

            if dec.action != "hold":
                amt = "100%" if dec.action == "sell_all" else dec.amount
                signals.append(Signal("sell", sym, amount=amt,
                                      reason=f"[{dec.tag}] {dec.reason}"))
                if dec.action == "sell_partial" and not pos.tp_done:
                    pos.tp_done = True

        # ---------- entries -----------------------------------------------
        universe = self._universe(portfolio)
        n_open = len(portfolio.positions)
        max_open = risk.risk_config.max_open_positions
        can_new, why = risk.can_open_new(risk.BreakerState(peak_equity=equity,
                                                           day_start_equity=equity))
        g_locked, g_why = prot.global_locked()
        if g_locked:
            return signals  # exits still matter while global lock is active

        btc_bearish = False
        try:
            bs = snapshot("btc", "1h", 250)
            btc_bearish = bool(bs and bs.ema_long and bs.price and bs.price < bs.ema_long)
        except Exception:
            pass

        scored_universe: list[tuple[float, str]] = []
        analyses: dict[str, Analysis] = {}
        meme_cache: dict[str, tuple[bool, str, Optional["mm.DegenScore"]]] = {}

        def _meme_check(sym: str):
            """Cache degen scorecard per symbol for this tick."""
            if sym in meme_cache:
                return meme_cache[sym]
            res = (False, "meme layer unavailable", None)
            if MEME_LAYER and _meme_mode() != "off" and mm.is_meme_candidate(sym):
                try:
                    net = os.getenv("MEME_DEFAULT_NETWORK", "solana")
                    res = mm.meme_gate(sym, network=net)
                except Exception as e:
                    res = (False, f"degen scan error ({type(e).__name__})", None)
            meme_cache[sym] = res
            return res

        # current degen sleeve usage (for the combined memecoin cap)
        def _sleeve_used() -> float:
            tot = 0.0
            for s, p in portfolio.positions.items():
                m, _, ds = _meme_check(s)
                is_m = risk.is_memecoin(s) or m
                if is_m:
                    tot += p.market_value(prices.get(s, p.avg_entry))
            return tot

        for sym in universe:
            if sym in portfolio.positions:
                continue
            a = analyze(sym)
            if a is None:
                continue
            analyses[sym] = a
            scored_universe.append((a.score_1h, sym))
        scored_universe.sort(reverse=True)

        max_buys = int(os.getenv("CONFLUENCE_MAX_BUYS_PER_TICK", "3"))
        buys = 0
        for _score, sym in scored_universe:
            if buys >= max_buys or n_open >= max_open:
                break
            a = analyses[sym]

            # ---- memecoin tier: DEX vetoes + own entry/stop rules --------
            meme_ok, meme_why, ds = _meme_check(sym)
            is_meme_tier = bool(ds is not None)   # went through the degen pipeline
            if is_meme_tier and risk.risk_config.meme_enabled:
                if not meme_ok:
                    signals.append(Signal("hold", sym, reason=f"degen veto: {meme_why}"))
                    continue
                # memes enter on momentum + buy pressure, not CEX confluence
                stop_price, take_profit, stop_pct = risk.meme_stop_and_target(a.price)
                ok, block_why = prot.can_enter(sym, records)
                if not ok:
                    signals.append(Signal("hold", sym, reason=f"protection: {block_why}"))
                    continue
                size = risk.position_size_usd(
                    equity, portfolio.cash,
                    signal_score=float(ds.score),
                    atr_pct=a.atr_pct,
                    stop_pct=stop_pct,
                    conviction=max(2, len(ds.pros)),
                    cluster_used_usd=_sleeve_used(),
                    total_exposure_usd=total_exposure,
                    open_positions=n_open,
                    btc_bearish=btc_bearish,
                    kelly_stats=risk.closed_trade_stats(portfolio.trades),
                    is_meme=True,
                    meme_sleeve_used_usd=_sleeve_used(),
                )
                usd = size.usd
                if usd < max(float(os.getenv("CONFLUENCE_MIN_BUY_USD", "10")), 0):
                    continue
                reason = (f"DEGEN {ds.score:.0f}/100 [{meme_why}] | "
                          f"SL ${stop_price:.6g} TP ${take_profit:.6g} | "
                          f"{'; '.join(size.reasons[:3])}")
                signals.append(Signal("buy", sym, usd_amount=round(usd, 2), reason=reason))
                _PLANNED[(self.account_id, sym)] = (stop_price, take_profit)
                buys += 1
                n_open += 1
                continue

            # ---- normal CEX confluence path ------------------------------
            enter, conviction, why_list = self._entry_signals(a)
            if not enter:
                continue

            ok, block_why = prot.can_enter(sym, records)
            if not ok:
                signals.append(Signal("hold", sym, reason=f"protection: {block_why}"))
                continue

            stop_price, take_profit, stop_pct = risk.stop_and_target(
                a.price, a.atr_value, side="long")
            cluster_used = 0.0
            try:
                cl = risk.cluster_of(sym)
                cluster_used = sum(p.market_value(prices.get(s, p.avg_entry))
                                   for s, p in portfolio.positions.items()
                                   if risk.cluster_of(s) == cl)
            except Exception:
                pass
            kstats = risk.closed_trade_stats(portfolio.trades)
            size = risk.position_size_usd(
                equity, portfolio.cash,
                signal_score=a.score_1h,
                atr_pct=a.atr_pct,
                stop_pct=stop_pct,
                conviction=conviction,
                cluster_used_usd=cluster_used,
                total_exposure_usd=total_exposure,
                open_positions=n_open,
                btc_bearish=btc_bearish,
                kelly_stats=kstats,
            )
            usd = size.usd
            if usd < max(float(os.getenv("CONFLUENCE_MIN_BUY_USD", "10")), 0):
                continue
            reason = (f"confluence {a.score_1h:+.0f} (4h {a.score_4h:+.0f}) | "
                      f"{'; '.join(why_list)} | SL ${stop_price:.6g} TP ${take_profit:.6g} "
                      f"| {'; '.join(size.reasons[:3])}")
            signals.append(Signal("buy", sym, usd_amount=round(usd, 2), reason=reason))
            # stash planned stops so executor can adopt them after fill
            _PLANNED[(self.account_id, sym)] = (stop_price, take_profit)
            buys += 1
            n_open += 1

        return signals


# planned stops per (account, symbol) picked up by auto_trader after a fill
_PLANNED: dict[tuple[str, str], tuple[float, float]] = {}


def planned_stops(account_id: str, symbol: str) -> Optional[tuple[float, float]]:
    return _PLANNED.pop((account_id, symbol.lower()), None)
