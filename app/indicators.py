"""Pine v7 と同じ計算を Python で行う。

用途：
  ・再検証（Pine の判定を Yahoo の日足から計算し直して食い違いを見る）
  ・チャート画像の反応点
  ・追跡（追跡は別モジュールだが、ここの ATR などを使う）

Pine 側の定義と対応：
  mid   = ta.linreg(close, len, 0)
  slope = mid - ta.linreg(close, len, 1)
  残差 d_i = close[i] - (mid - slope*i)   i=0 が最新
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass
class Bar:
    date: str      # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Channel:
    length: int
    mid: float
    slope: float
    dev: float
    max_abs: float
    pct_off: float
    off: float = 0.0
    upper: float = 0.0
    lower: float = 0.0
    pos_pct: Optional[float] = None
    width_pct: Optional[float] = None
    width_atr: Optional[float] = None


@dataclass
class Touches:
    up: int = 0
    dn: int = 0
    up_offsets: List[int] = field(default_factory=list)   # ゾーンに入った足（0=最新）
    dn_offsets: List[int] = field(default_factory=list)


# ---------------- 基本 ----------------
def sma(values: Sequence[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        out.append(s / period if i >= period - 1 else None)
    return out


def atr_wilder(bars: Sequence[Bar], period: int = 14) -> List[Optional[float]]:
    """Pine の ta.atr と同じ（RMA）。"""
    out: List[Optional[float]] = []
    prev_close: Optional[float] = None
    trs: List[float] = []
    rma: Optional[float] = None
    for i, b in enumerate(bars):
        tr = b.high - b.low if prev_close is None else max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        prev_close = b.close
        trs.append(tr)
        if rma is None:
            if len(trs) >= period:
                rma = sum(trs[-period:]) / period
                out.append(rma)
            else:
                out.append(None)
        else:
            rma = (rma * (period - 1) + tr) / period
            out.append(rma)
    return out


def linreg_channel(closes: Sequence[float], length: int, pctile: float = 90.0) -> Optional[Channel]:
    if len(closes) < length or length < 2:
        return None
    y = list(closes[-length:])          # 古い→新しい
    n = length
    xs = list(range(n))
    mean_x = (n - 1) / 2.0
    mean_y = sum(y) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((xs[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    b = sxy / sxx if sxx else 0.0
    a = mean_y - b * mean_x
    mid = a + b * (n - 1)
    slope = b
    res_abs: List[float] = []
    ss = 0.0
    mx = 0.0
    for i in range(n):                  # i=0 が最新
        c = y[n - 1 - i]
        d = c - (mid - slope * i)
        ss += d * d
        mx = max(mx, abs(d))
        res_abs.append(abs(d))
    dev = math.sqrt(ss / n)
    pct = percentile_linear(res_abs, pctile)
    return Channel(length=n, mid=mid, slope=slope, dev=dev, max_abs=mx, pct_off=pct)


def percentile_linear(values: Sequence[float], p: float) -> float:
    """Pine の array.percentile_linear_interpolation と同じ（線形補間）。"""
    if not values:
        return float("nan")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = (p / 100.0) * (len(s) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (rank - lo)


def finish_channel(ch: Channel, off: float, close: float, atr: Optional[float]) -> Channel:
    ch.off = off
    ch.upper = ch.mid + off
    ch.lower = ch.mid - off
    ch.pos_pct = (close - ch.lower) / (2.0 * off) * 100.0 if off > 0 else None
    ch.width_pct = 2.0 * off / close * 100.0 if off > 0 and close else None
    ch.width_atr = 2.0 * off / atr if off > 0 and atr else None
    return ch


def touches(bars: Sequence[Bar], ch: Channel, zone_pct: float = 15.0, exit_pct: float = 35.0) -> Touches:
    """端への反応点。ゾーンに入って戻ったら1回（Pine と同じ）。"""
    t = Touches()
    if ch.off <= 0 or len(bars) < ch.length:
        return t
    w = 2.0 * ch.off
    in_lo = in_hi = False
    lo_enter = hi_enter = 0
    for i in range(ch.length - 1, -1, -1):   # 古い→新しい
        b = bars[-1 - i]
        lo_i = (ch.mid - ch.slope * i) - ch.off
        p_low = (b.low - lo_i) / w * 100.0
        p_high = (b.high - lo_i) / w * 100.0
        p_cl = (b.close - lo_i) / w * 100.0
        if not in_lo and p_low <= zone_pct:
            in_lo = True
            lo_enter = i
        elif in_lo and p_cl >= exit_pct:
            in_lo = False
            t.dn += 1
            t.dn_offsets.append(lo_enter)
        if not in_hi and p_high >= 100.0 - zone_pct:
            in_hi = True
            hi_enter = i
        elif in_hi and p_cl <= 100.0 - exit_pct:
            in_hi = False
            t.up += 1
            t.up_offsets.append(hi_enter)
    return t


def efficiency_ratio(closes: Sequence[float], length: int) -> Optional[float]:
    if len(closes) < length:
        return None
    seg = closes[-length:]
    den = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    if den <= 0:
        return None
    return abs(seg[-1] - seg[0]) / den


def weekly_closes(bars: Sequence[Bar]) -> List[float]:
    """日足を週（月〜日）にまとめた終値。最後の要素は「今の週」（未完了の可能性あり）。"""
    out: List[Tuple[Tuple[int, int], float]] = []
    for b in bars:
        d = datetime.strptime(b.date, "%Y-%m-%d").date()
        key = d.isocalendar()[:2]
        if out and out[-1][0] == key:
            out[-1] = (key, b.close)
        else:
            out.append((key, b.close))
    return [c for _, c in out]


def pct_change(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1.0) * 100.0


@dataclass
class Recomputed:
    channel: Channel
    channel_a: Channel
    touches: Touches
    atr: Optional[float]
    slope_atr: Optional[float]
    eff_ratio: Optional[float]
    stability: Optional[float]
    ma: Dict[str, Optional[float]]
    ma20_chg5_pct: Optional[float]
    ma100_chg20_pct: Optional[float]
    w_ma5_chg1_pct: Optional[float]
    w_ma10_chg1_pct: Optional[float]
    above_ma100: bool
    base_class: str
    l1_flat: bool
    l2_width: bool
    l2_stab: bool


def recompute(bars: Sequence[Bar], length: int, width_type: str, params: Optional[Dict[str, float]] = None) -> Optional[Recomputed]:
    """Pine v7 の三層の数字を日足から計算し直す。params は Pine の入力値と合わせる。"""
    # 既定値は Pine の入力の既定と一致させておくこと。
    # ここがずれると再計算が毎回「食い違い」になり、確認の仕組みが役に立たなくなる。
    # 通常は Pine が params を送ってくるので、この既定は古い Pine 用の受け皿。
    p = {
        "sigma_mult": 2.0, "pctile": 90.0, "max_slope_atr": 0.10, "max_eff_ratio": 0.35,
        "flat_thr": -0.5, "touch_zone": 15.0, "touch_exit": 35.0, "min_width_pct": 0.0,
        "min_width_atr": 3.0, "max_width_atr": 15.0, "stab_delta": 5, "min_stability": 0.7,
    }
    if params:
        p.update(params)
    if len(bars) < max(length + int(p["stab_delta"]), 101):
        return None
    closes = [b.close for b in bars]
    atr_series = atr_wilder(bars, 14)
    atr = atr_series[-1]
    close = closes[-1]

    def width_of(ch: Channel) -> float:
        if width_type.startswith("stdev"):
            try:
                mult = float(width_type.replace("stdev", "") or p["sigma_mult"])
            except ValueError:
                mult = p["sigma_mult"]
            return ch.dev * mult
        if width_type.startswith("pct"):
            try:
                q = float(width_type.replace("pct", "") or p["pctile"])
            except ValueError:
                q = p["pctile"]
            return percentile_linear(_residuals(closes, ch), q)
        return ch.max_abs   # raff

    ch = linreg_channel(closes, length, p["pctile"])
    if ch is None:
        return None
    finish_channel(ch, width_of(ch), close, atr)
    ch_a = linreg_channel(closes, length, p["pctile"])
    finish_channel(ch_a, ch_a.max_abs, close, atr)

    delta = int(p["stab_delta"])
    ch_p = linreg_channel(closes, length + delta, p["pctile"])
    ch_m = linreg_channel(closes, max(10, length - delta), p["pctile"])
    stability = None
    if ch_p and ch_m and atr and ch.off > 0:
        off_p = width_of(ch_p)
        off_m = width_of(ch_m)
        stab_slope = max(abs(ch.slope - ch_p.slope), abs(ch.slope - ch_m.slope)) / atr
        stab_width = max(abs(off_p / ch.off - 1.0), abs(off_m / ch.off - 1.0))
        stability = 1.0 - min(1.0, max(stab_slope / p["max_slope_atr"], stab_width))

    t = touches(bars, ch, p["touch_zone"], p["touch_exit"])
    slope_atr = abs(ch.slope) / atr if atr else None
    er = efficiency_ratio(closes, length)
    l1_flat = slope_atr is not None and er is not None and slope_atr <= p["max_slope_atr"] and er <= p["max_eff_ratio"]

    ma: Dict[str, Optional[float]] = {}
    series: Dict[int, List[Optional[float]]] = {}
    for n in (5, 10, 20, 50, 100):
        series[n] = sma(closes, n)
        ma["ma%d" % n] = series[n][-1]
    ma20_chg5 = pct_change(series[20][-1], series[20][-6]) if len(closes) > 25 else None
    ma100_chg20 = pct_change(series[100][-1], series[100][-21]) if len(closes) > 120 else None

    wc = weekly_closes(bars)
    completed = wc[:-1]   # Pine の [1] = 前の完了週
    w5 = sma(completed, 5)
    w10 = sma(completed, 10)
    w_ma5_chg = pct_change(w5[-1], w5[-2]) if len(w5) >= 2 else None
    w_ma10_chg = pct_change(w10[-1], w10[-2]) if len(w10) >= 2 else None

    above100 = ma["ma100"] is not None and close > ma["ma100"]
    ma20_flat_up = ma20_chg5 is not None and ma20_chg5 >= p["flat_thr"]
    weekly_ok = w_ma5_chg is not None and w_ma10_chg is not None and w_ma5_chg >= p["flat_thr"] and w_ma10_chg >= p["flat_thr"]
    if above100 and weekly_ok:
        base_class = "uptrend_pause"
    elif above100:
        base_class = "directionless"
    elif ma20_flat_up or (w_ma5_chg is not None and w_ma5_chg >= p["flat_thr"]):
        base_class = "bottoming"
    else:
        base_class = "downtrend"

    l2_width = ch.width_pct is not None and ch.width_atr is not None and ch.width_pct >= p["min_width_pct"] and p["min_width_atr"] <= ch.width_atr <= p["max_width_atr"]
    l2_stab = stability is not None and stability >= p["min_stability"]

    return Recomputed(
        channel=ch, channel_a=ch_a, touches=t, atr=atr, slope_atr=slope_atr, eff_ratio=er, stability=stability,
        ma=ma, ma20_chg5_pct=ma20_chg5, ma100_chg20_pct=ma100_chg20, w_ma5_chg1_pct=w_ma5_chg, w_ma10_chg1_pct=w_ma10_chg,
        above_ma100=above100, base_class=base_class, l1_flat=l1_flat, l2_width=l2_width, l2_stab=l2_stab,
    )


def _residuals(closes: Sequence[float], ch: Channel) -> List[float]:
    n = ch.length
    y = closes[-n:]
    return [abs(y[n - 1 - i] - (ch.mid - ch.slope * i)) for i in range(n)]
