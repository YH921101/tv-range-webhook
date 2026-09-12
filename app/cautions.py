"""注意点（発火は止めないが、目で見るときに知っておきたいこと）。

書くのは事実だけ。「勢いが出にくい」といった解釈は付けない。
解釈を混ぜると AI がそれをそのまま繰り返してしまい、根拠のない断定になる。

方針：発火の判定はレンジの形だけで行う（Pine 側）。
出来高・ギャップ・端の外・土台の向きなどは、ここで文章にして
Discord の通知と AI コメントに渡す。止める側には一切使わない。

例外として Render 側で配信を止めているのは、品質・土台・同じ日の重複の
三つだけ（pipeline.decide_delivery）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .indicators import Bar

# しきい値（注意点を出すかどうかだけに使う。発火には影響しない）
GAP_PCT = 10.0          # 前日終値→当日始値
GAP_ATR_MULT = 3.0
VOL_SPIKE_20 = 1.8      # 20日平均に対する倍率
VOL_THIN_20 = 0.6
WIDE_ATR = 10.0         # 幅が ATR の何倍を超えたら「荒い」
NARROW_ATR = 3.0


def _f(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def recompute_context(bars: Sequence[Bar]) -> Dict[str, Any]:
    """Yahoo の日足から、ギャップと出来高の倍率を出し直す。

    一括アラート（軽い JSON）ではこれらが送られてこないので、ここで埋める。
    """
    out: Dict[str, Any] = {}
    if not bars or len(bars) < 2:
        return out
    last, prev = bars[-1], bars[-2]
    if prev.close:
        out["gap_pct"] = abs(last.open - prev.close) / prev.close * 100.0
        out["change_pct"] = (last.close / prev.close - 1) * 100.0
    if prev.volume:
        out["vol_ratio_1"] = last.volume / prev.volume
    tail = [b.volume for b in bars[-21:-1] if b.volume]
    if tail:
        avg = sum(tail) / len(tail)
        if avg > 0:
            out["vol_ratio_20"] = last.volume / avg
    return out


def build(row: Dict[str, Any], bars: Optional[Sequence[Bar]] = None) -> List[str]:
    """注意点の一覧。空なら特筆なし。"""
    ctx = recompute_context(bars or [])
    notes: List[str] = []

    # --- ギャップ（乖離の大きさ） ---
    gap = _f(row.get("gap_pct"))
    if gap is None:
        gap = _f(ctx.get("gap_pct"))
    atr, price = _f(row.get("atr")), _f(row.get("price"))
    atr_pct = (atr / price * 100.0) if (atr and price) else None
    if gap is not None:
        by_pct = gap >= GAP_PCT
        by_atr = atr_pct is not None and atr_pct > 0 and gap >= atr_pct * GAP_ATR_MULT
        if by_pct or by_atr:
            tail = "（1日の値幅の %s倍）" % _r(gap / atr_pct) if atr_pct else ""
            notes.append("前日終値から %s%% 離れて始まった%s" % (_r(gap), tail))

    # --- 出来高 ---
    v20 = _f(row.get("vol_ratio_20")) or _f(ctx.get("vol_ratio_20"))
    v1 = _f(row.get("vol_ratio_1")) or _f(ctx.get("vol_ratio_1"))
    if v20 is not None:
        if v20 >= VOL_SPIKE_20:
            notes.append("出来高が20日平均の %s倍" % _r(v20))
        elif v20 <= VOL_THIN_20:
            notes.append("出来高が20日平均の %s倍" % _r(v20))
    if v1 is not None and v1 >= 3.0:
        notes.append("出来高が前日の %s倍" % _r(v1))

    # --- 端の外 ---
    pos = _f(row.get("pos_pct"))
    if pos is not None:
        if pos < 0:
            notes.append("下端の外で引けた（位置 -%s%%）" % _r(abs(pos)))
        elif pos > 100:
            notes.append("上端の外で引けた（位置 %s%%）" % _r(pos))

    # --- レンジの形そのもの ---
    w = _f(row.get("width_atr"))
    if w is not None:
        if w >= WIDE_ATR:
            notes.append("枠の幅が %s ATR（広い部類）" % _r(w))
        elif w <= NARROW_ATR:
            notes.append("枠の幅が %s ATR（狭い部類）" % _r(w))
    stab = _f(row.get("stability"))
    if stab is not None and stab < 0.7:
        notes.append("枠の安定度 %s（0.6が下限）" % _r(stab, 2))

    # --- 土台 ---
    if (row.get("base_class") or "") == "downtrend":
        notes.append("土台の分類は下落中")
    if row.get("above_ma100") == 0:
        notes.append("100日移動平均より下で推移")

    # --- 再計算との食い違い ---
    if row.get("verify_status") == "mismatch":
        notes.append("Render の再計算と数字が一致しない")

    return notes[:4]


def _r(v: Optional[float], digits: int = 1) -> str:
    if v is None:
        return "-"
    return ("%." + str(digits) + "f") % v


def text(notes: Sequence[str]) -> str:
    return " / ".join(notes)
