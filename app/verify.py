"""Render 側の再検証。Pine の数字を Yahoo の日足から計算し直し、食い違いを記録する。

旧 Render の validate_signal_consistency と同じ思想。落とすためではなく、
Pine の書き間違いや版ずれ、データのずれを自動で見つけるためのもの。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from . import config
from .indicators import Bar, Recomputed, recompute


def verify_row(row: Dict[str, Any], bars: List[Bar]) -> Dict[str, Any]:
    """戻り値: {"status": match|mismatch|skipped, "diffs": [...], "recomputed": {...}}"""
    length = int(row.get("ch_len") or 0)
    width_type = row.get("width_type") or "stdev2"
    if not bars or length < 10:
        return {"status": "skipped", "reason": "no bars or bad length"}
    if bars[-1].date != row.get("bar_date"):
        return {"status": "skipped", "reason": "last bar date %s != %s" % (bars[-1].date, row.get("bar_date"))}
    # Pine が送ってきた入力値で計算し直す。無ければ既定（Pine の既定と揃えてある）。
    rc = recompute(bars, length, width_type, row.get("_params") or None)
    if rc is None:
        return {"status": "skipped", "reason": "not enough bars"}

    diffs: List[str] = []

    def num(name: str, ours: Optional[float], theirs: Optional[float], tol: float) -> None:
        if ours is None or theirs is None:
            return
        if abs(ours - theirs) > tol:
            diffs.append("%s: pine=%.2f render=%.2f" % (name, theirs, ours))

    num("pos_pct", rc.channel.pos_pct, row.get("pos_pct"), config.VERIFY_POS_TOL)
    num("upper", rc.channel.upper, row.get("ch_upper"), max(0.5, (row.get("price") or 0) * 0.004))
    num("lower", rc.channel.lower, row.get("ch_lower"), max(0.5, (row.get("price") or 0) * 0.004))
    num("slope_atr", rc.slope_atr, row.get("slope_atr"), 0.03)
    num("eff_ratio", rc.eff_ratio, row.get("eff_ratio"), 0.08)
    num("stability", rc.stability, row.get("stability"), 0.15)
    tu, td = row.get("touch_up"), row.get("touch_dn")
    if tu is not None and abs(rc.touches.up - int(tu)) > config.VERIFY_TOUCH_TOL:
        diffs.append("touch_up: pine=%s render=%d" % (tu, rc.touches.up))
    if td is not None and abs(rc.touches.dn - int(td)) > config.VERIFY_TOUCH_TOL:
        diffs.append("touch_dn: pine=%s render=%d" % (td, rc.touches.dn))
    if row.get("base_class") and rc.base_class != row.get("base_class"):
        diffs.append("base_class: pine=%s render=%s" % (row.get("base_class"), rc.base_class))
    if row.get("gate_l1_flat") is not None and bool(row.get("gate_l1_flat")) != rc.l1_flat:
        diffs.append("l1_flat: pine=%s render=%s" % (bool(row.get("gate_l1_flat")), rc.l1_flat))

    status = "match" if not diffs else "mismatch"
    return {"status": status, "diffs": diffs, "recomputed": summarize(rc)}


def summarize(rc: Recomputed) -> Dict[str, Any]:
    return {
        "upper": round(rc.channel.upper, 2), "lower": round(rc.channel.lower, 2), "mid": round(rc.channel.mid, 2),
        "pos_pct": None if rc.channel.pos_pct is None else round(rc.channel.pos_pct, 2),
        "touch_up": rc.touches.up, "touch_dn": rc.touches.dn,
        "slope_atr": None if rc.slope_atr is None else round(rc.slope_atr, 3),
        "eff_ratio": None if rc.eff_ratio is None else round(rc.eff_ratio, 3),
        "stability": None if rc.stability is None else round(rc.stability, 3),
        "base_class": rc.base_class, "l1_flat": rc.l1_flat, "l2_width": rc.l2_width, "l2_stab": rc.l2_stab,
        "atr": None if rc.atr is None else round(rc.atr, 2),
    }
