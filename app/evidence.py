"""チャネル上端・下端の「根拠」を組み立てる。

旧 Render の支持抵抗エンジン（MA・キリ番・実体高安・出来高帯を 1.5％幅でまとめ、点数で強弱）を、
期待値の計算元ではなく「なぜそこが端なのか」の説明に回したもの。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

SCORE_ROUND = 2.0
SCORE_MA = {"5MA": 2.0, "10MA": 1.0, "20MA": 2.0, "50MA": 2.0, "100MA": 2.0}
SCORE_BODY = 2.0
SCORE_VZ = {"vz1": 2.0, "vz2": 1.5, "vz3": 1.0}
NEAR_PCT = 2.5           # 端から何％以内を「その端の根拠」とみなすか
NEAR_ATR = 0.5           # 同上を1日の値幅（ATR）で見たときの倍率。値動きの大きい銘柄はこちらが効く


def _cands(row: Dict[str, Any]) -> List[Tuple[float, float, float, str, float]]:
    """(low, high, mid, label, score)"""
    out: List[Tuple[float, float, float, str, float]] = []

    def pt(v: Optional[float], label: str, score: float) -> None:
        if v is None or v <= 0:
            return
        out.append((v, v, v, label, score))

    pt(row.get("round_up"), "キリ番", SCORE_ROUND)
    pt(row.get("round_down"), "キリ番", SCORE_ROUND)
    for key, label in (("ma5", "5MA"), ("ma10", "10MA"), ("ma20", "20MA"), ("ma50", "50MA"), ("ma100", "100MA")):
        pt(row.get(key), label, SCORE_MA[label])
    for key, label in (("body_high_20", "20日実体高値"), ("body_high_40", "40日実体高値"), ("body_high_60", "60日実体高値"),
                       ("body_low_20", "20日実体安値"), ("body_low_40", "40日実体安値"), ("body_low_60", "60日実体安値")):
        pt(row.get(key), label, SCORE_BODY)
    for key, label in (("vz1", "直近20日出来高帯"), ("vz2", "21〜40日出来高帯"), ("vz3", "41〜60日出来高帯")):
        lo, hi, ratio = row.get(key + "_low"), row.get(key + "_high"), row.get(key + "_ratio")
        if lo is None or hi is None or lo <= 0 or hi <= 0:
            continue
        if hi < lo:
            lo, hi = hi, lo
        mult = 2.0 if (ratio or 0) >= 2.0 else 1.5 if (ratio or 0) >= 1.5 else 1.0
        text = label + ("(%.1f倍)" % ratio if ratio else "")
        out.append((lo, hi, (lo + hi) / 2, text, SCORE_VZ[key] * mult))
    return out


def reasons_for_edges(row: Dict[str, Any]) -> Tuple[str, str, float, float]:
    """(upper_reason, lower_reason, upper_score, lower_score)"""
    upper, lower, price = row.get("ch_upper"), row.get("ch_lower"), row.get("price")
    if not upper or not lower or not price:
        return "", "", 0.0, 0.0
    # 完全一致はしないので幅を持たせる。値動きの小さい銘柄は％、大きい銘柄は ATR が効くよう、大きいほうを採る。
    atr = row.get("atr") or 0.0
    band = max(price * NEAR_PCT / 100.0, float(atr) * NEAR_ATR)
    up_labels: List[str] = []
    lo_labels: List[str] = []
    up_score = lo_score = 0.0
    for lo, hi, mid, label, score in _cands(row):
        # 帯（出来高帯）は端と重なれば採用、点は端から band 以内
        if lo - band <= upper <= hi + band:
            if label not in up_labels:
                up_labels.append(label)
                up_score += score
        if lo - band <= lower <= hi + band:
            if label not in lo_labels:
                lo_labels.append(label)
                lo_score += score
    return "＋".join(up_labels), "＋".join(lo_labels), up_score, lo_score


def strength_label(score: float) -> str:
    if score >= 6.0:
        return "強"
    if score >= 3.0:
        return "中"
    if score > 0:
        return "弱"
    return "-"


# =====================================================
# 信頼度 ── いま引いている上端・下端の位置がどれだけ信用できるか
# -----------------------------------------------------
# すべて加点。条件を満たさなければ 0 点になるだけで、引かない。
# 「発火するかどうか」は Pine が枠の形だけで決めている。ここはそのあとで、
# 買う側の端がどれだけ確かかを積み上げて見せるためのもの。
#
# 位置％（端にどれだけ近いか）は加点に入れない。狙い方の話であって
# 端の確かさの話ではなく、位置％は通知に別に出ているので二重になる。
# =====================================================
EDGE_SCORE_CAP = 10.0        # 端に重なる支持抵抗の点数は10点で打ち切る
TOUCH_BONUS_FROM = 2         # 反応点が何回を超えたら加点を始めるか（2回は発火の最低条件）
TOUCH_BONUS_MAX = 3
STAB_HIGH, STAB_MID = 0.80, 0.70
LABEL_STRONG, LABEL_MID = 12.0, 6.0


def confidence(row: Dict[str, Any], upper_score: float = 0.0, lower_score: float = 0.0) -> Dict[str, Any]:
    """買う側の端がどれだけ確かかを、加点だけで積み上げる。

    戻り値 {"score": 合計, "label": 強/中/弱, "detail": 内訳の文章}
    upper_score / lower_score は reasons_for_edges が返した端の点数。
    """
    is_long = (row.get("c_side") or "LONG").upper() != "SHORT"
    edge_name = "下端" if is_long else "上端"
    edge_reason = str((row.get("lower_reason") if is_long else row.get("upper_reason")) or "")
    edge_score = _f(lower_score if is_long else upper_score) or 0.0

    parts: List[str] = []
    score = 0.0

    # ① 端に重なる支持抵抗の厚み
    base = min(max(edge_score, 0.0), EDGE_SCORE_CAP)
    if base > 0:
        score += base
        # 目印の名前は「下端の根拠」の行に既に出ているので、ここでは重なりの数だけにする。
        # 内訳は足し算が追えることが大事で、名前を繰り返すと行が読めなくなる。
        head = edge_reason.split("［")[0]
        n = len([x for x in head.split("＋") if x.strip()]) if head else 0
        capped = "・上限" if edge_score > EDGE_SCORE_CAP else ""
        parts.append("%sの支持%.0f（重なり%d件%s）" % (edge_name, base, n, capped) if n
                     else "%sの支持%.0f" % (edge_name, base))

    # ② キリ番が混じっているか。誰の目にも同じに見える価格は効きやすい
    if "キリ番" in edge_reason:
        score += 1.0
        parts.append("キリ番+1")

    # ③ その端で実際に止まった回数。3回目以降を加点
    touches = int(_f(row.get("touch_dn") if is_long else row.get("touch_up")) or 0)
    extra = min(max(touches - TOUCH_BONUS_FROM, 0), TOUCH_BONUS_MAX)
    if extra > 0:
        score += float(extra)
        parts.append("反応%d回目+%d" % (touches, extra))

    # ④ 枠の線が動いていないか
    stab = _f(row.get("stability"))
    if stab is not None:
        if stab >= STAB_HIGH:
            score += 2.0
            parts.append("安定度%.2fで+2" % stab)
        elif stab >= STAB_MID:
            score += 1.0
            parts.append("安定度%.2fで+1" % stab)

    # ⑤ 大きな流れに逆らっていないか（買いは100日線の上、売りは下）
    above = row.get("above_ma100")
    if above is not None:
        aligned = (int(above) == 1) if is_long else (int(above) == 0)
        if aligned:
            score += 1.0
            parts.append(("100MA上+1" if is_long else "100MA下+1"))

    # ⑥ Yahoo の日足で引き直しても同じ数字になったか
    if row.get("verify_status") == "match":
        score += 1.0
        parts.append("再計算一致+1")

    label = "強" if score >= LABEL_STRONG else "中" if score >= LABEL_MID else "弱"
    return {"score": round(score, 1), "label": label, "detail": "＋".join(parts)}


def _f(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x
