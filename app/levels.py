"""目印（支持抵抗）が重なっている価格帯を、地図として並べる。

ねらいは3つ。
  ・枠の中 …… 上端まで素直に行けそうか、途中で引っかかりそうか
  ・抜けた先 … 上端を抜けたあと、次にどこで止まりそうか
  ・割れた先 … 下端を割ったあと、どこで下げ止まりそうか

期待値は計算しない。発火の判定にも使わない。

旧 Render のゾーンエンジンを、この用途に必要な分だけ移植したもの。
違いは3つ。
  ・期待値（EV）とEVランクを落とした
  ・枠の中と枠の外を区別して並べる
  ・端そのものに重なる節目は外す。それは既に「上端の根拠／下端の根拠」として出ている
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import evidence

GROUP_PCT = 3.0          # この幅に収まる候補はひとつの帯にまとめる（現在値に対する％）
ZONE_MAX_PCT = 3.0       # 帯の幅の上限
ZONE_MIN_PCT = 0.6       # 点だけの帯にも、見やすさのため最小の幅を持たせる
IN_PER_SIDE = 3          # 枠の中、上と下それぞれ何本まで出すか
OUT_PER_SIDE = 2         # 枠の外、抜けた先と割れた先それぞれ何本まで
OUT_RANGE_PCT = 20.0     # 枠の外は現在値からこの％までを見る（遠すぎるものは役に立たない）
EDGE_SKIP_PCT = 1.5      # 端からこの％以内に重なる節目は「端の根拠」なので出さない
EDGE_SKIP_ATR = 0.4      # 同上を1日の値幅で見たときの倍率。大きいほうを採る

# 表示の並び順と見出し
ZONE_ORDER = ["in_up", "in_down", "out_up", "out_down"]
ZONE_JP = {"in_up": "枠の中 上", "in_down": "枠の中 下", "out_up": "抜けた先", "out_down": "割れた先"}
ZONE_AI = {"in_up": "枠の中で現在値より上", "in_down": "枠の中で現在値より下",
           "out_up": "上端より上", "out_down": "下端より下"}


def build(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """目印が重なっている価格帯。枠の中・抜けた先・割れた先の順に返す。"""
    price = evidence._f(row.get("price"))
    upper = evidence._f(row.get("ch_upper"))
    lower = evidence._f(row.get("ch_lower"))
    if not price or price <= 0 or upper is None or lower is None or upper <= lower:
        return []

    atr = evidence._f(row.get("atr")) or 0.0
    edge_band = max(price * EDGE_SKIP_PCT / 100.0, atr * EDGE_SKIP_ATR)

    buckets: Dict[str, List[Dict[str, Any]]] = {k: [] for k in ZONE_ORDER}
    for lo, hi, mid, label, score in evidence._cands(row):
        # 端そのものに重なるものは「端の根拠」として既に出ている
        if abs(mid - lower) <= edge_band or abs(mid - upper) <= edge_band:
            continue
        if lo - edge_band <= lower <= hi + edge_band or lo - edge_band <= upper <= hi + edge_band:
            continue
        # 現在値ちょうどのものは上下どちらとも言えないので落とす
        if lo <= price <= hi:
            continue
        if hi <= lower:
            key = "out_down"
        elif lo >= upper:
            key = "out_up"
        else:
            key = "in_up" if mid > price else "in_down"
        buckets[key].append({"low": lo, "high": hi, "mid": mid, "label": label, "score": score})

    out: List[Dict[str, Any]] = []
    for key in ZONE_ORDER:
        is_up = key in ("in_up", "out_up")
        limit = IN_PER_SIDE if key.startswith("in_") else OUT_PER_SIDE
        zones = _group(buckets[key], price)
        for z in zones:
            z["zone"] = key
            z["side"] = "up" if is_up else "down"
            z["distance_pct"] = ((z["low"] / price - 1.0) if is_up else (1.0 - z["high"] / price)) * 100.0
        zones = [z for z in zones if z["distance_pct"] >= 0]
        if key.startswith("out_"):
            zones = [z for z in zones if z["distance_pct"] <= OUT_RANGE_PCT]
        zones.sort(key=lambda z: z["distance_pct"])
        out.extend(zones[:limit])
    return out


def _group(cands: List[Dict[str, Any]], price: float) -> List[Dict[str, Any]]:
    if not cands:
        return []
    width = price * GROUP_PCT / 100.0
    max_width = price * ZONE_MAX_PCT / 100.0
    min_width = price * ZONE_MIN_PCT / 100.0

    groups: List[Dict[str, Any]] = []
    for c in sorted(cands, key=lambda x: x["mid"]):
        if groups:
            g = groups[-1]
            merged_lo, merged_hi = min(g["low"], c["low"]), max(g["high"], c["high"])
            if c["low"] <= g["high"] + width and merged_hi - merged_lo <= max_width:
                g["low"], g["high"] = merged_lo, merged_hi
                g["score"] += c["score"]
                if c["label"] not in g["labels"]:
                    g["labels"].append(c["label"])
                continue
        groups.append({"low": c["low"], "high": c["high"], "score": c["score"], "labels": [c["label"]]})

    for g in groups:
        if g["high"] - g["low"] < min_width:
            center = (g["low"] + g["high"]) / 2.0
            g["low"], g["high"] = center - min_width / 2.0, center + min_width / 2.0
        g["label_text"] = "＋".join(g["labels"])
        g["match_count"] = len(g["labels"])
        g["strength"] = evidence.strength_label(g["score"])
    return groups


def text_lines(zones: List[Dict[str, Any]], yen) -> List[str]:
    """Discord に出す行。yen は金額の書式を作る関数を渡す。"""
    lines: List[str] = []
    for z in zones:
        lines.append("　%s %s〜%s（%s）［%s］ %s%%" % (
            ZONE_JP.get(z.get("zone") or "", ""), yen(z["low"]), yen(z["high"]),
            z["label_text"], z["strength"],
            ("+" if z["side"] == "up" else "-") + ("%.1f" % abs(z["distance_pct"]))))
    return lines


def ai_text(zones: List[Dict[str, Any]]) -> str:
    """AI に渡すときの一行。価格・距離・重なりだけ。強弱の点数は渡さない。"""
    if not zones:
        return ""
    items = []
    for z in zones:
        items.append("%s %.0f〜%.0f（%s、%s%.1f%%）" % (
            ZONE_AI.get(z.get("zone") or "", ""), z["low"], z["high"], z["label_text"],
            "+" if z["side"] == "up" else "-", abs(z["distance_pct"])))
    return " / ".join(items)
