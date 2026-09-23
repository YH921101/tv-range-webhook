"""Pine v7 の JSON を受け取り、検証して signals の1行に平らにする。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import config


def to_float(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def to_int(v: Any) -> Optional[int]:
    f = to_float(v)
    return int(f) if f is not None else None


def to_bool_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return 1 if v else 0
    return 1 if str(v).lower() in ("true", "1", "yes") else 0


def s(v: Any) -> str:
    return "" if v is None else str(v)


def validate(payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """(errors, warnings)。errors があれば保存はするが配信しない。"""
    errors: List[str] = []
    warnings: List[str] = []
    if "raw_body" in payload:
        return ["JSON として読めない本文です。TradingView のアラート設定を確認してください。"], warnings
    if to_int(payload.get("v")) != config.EXPECTED_PAYLOAD_V:
        errors.append("payload の版が違います。expected v=%d actual=%s" % (config.EXPECTED_PAYLOAD_V, payload.get("v")))
    # Pine の版は記録用。EXPECTED_PINE_VERSION を入れたときだけ照合し、不一致でも止めない
    pv = s(payload.get("pine_version"))
    if config.EXPECTED_PINE_VERSION and pv != config.EXPECTED_PINE_VERSION:
        warnings.append("Pine の版が想定と違います。expected=%s actual=%s" % (config.EXPECTED_PINE_VERSION, pv or "未送信"))
    for key in ("signal_id", "symbol", "bar_date", "mode"):
        if not s(payload.get(key)).strip():
            errors.append("必須項目が不足しています: " + key)
    price = to_float(payload.get("price"))
    if price is None or price <= 0:
        errors.append("price が不足、または0以下です。")
    for key in ("channel", "features", "base", "gates", "signal"):
        if not isinstance(payload.get(key), dict):
            errors.append("入れ子の項目が不足しています: " + key)
    if not isinstance(payload.get("shadow"), list):
        warnings.append("shadow（方式A/B）が未送信です。")
    if not isinstance(payload.get("evidence"), dict):
        warnings.append("evidence（根拠）が未送信です。")
    if s(payload.get("mode")) not in ("signal", "snapshot", "preview"):
        errors.append("mode が想定外です: " + s(payload.get("mode")))
    try:
        datetime.strptime(s(payload.get("bar_date")), "%Y-%m-%d")
    except ValueError:
        errors.append("bar_date が YYYY-MM-DD ではありません: " + s(payload.get("bar_date")))
    return errors, warnings


def flatten(payload: Dict[str, Any], received_at: str) -> Dict[str, Any]:
    ch = payload.get("channel") or {}
    ft = payload.get("features") or {}
    bs = payload.get("base") or {}
    gt = payload.get("gates") or {}
    sg = payload.get("signal") or {}
    ev = payload.get("evidence") or {}
    shadow = {str(x.get("method")): x for x in (payload.get("shadow") or []) if isinstance(x, dict)}
    a = shadow.get("A", {})
    b = shadow.get("B", {})
    pr = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    vz1 = ev.get("volzone_1_20") or {}
    vz2 = ev.get("volzone_21_40") or {}
    vz3 = ev.get("volzone_41_60") or {}

    row: Dict[str, Any] = {
        # Pine がいま使っている入力値。再計算を同じ設定で回すためのもの。
        # DB の列ではないので保存はされない（payload_json には元のまま残る）。
        "_params": {k: v for k, v in ((k2, to_float(pr.get(k2))) for k2 in (
            "sigma_mult", "pctile", "max_slope_atr", "max_eff_ratio", "flat_thr",
            "touch_zone", "touch_exit", "min_width_pct", "min_width_atr",
            "max_width_atr", "stab_delta", "min_stability")) if v is not None},

        "signal_id": s(payload.get("signal_id")),
        "received_at": received_at,
        "bar_date": s(payload.get("bar_date")),
        "mode": s(payload.get("mode")) or "signal",
        "pine_version": s(payload.get("pine_version")),
        "symbol": normalize_symbol(payload.get("symbol")),
        "name": s(payload.get("name")) or None,
        "exchange": s(payload.get("exchange")) or None,
        "tickerid": s(payload.get("tickerid")) or None,
        "tv_url": s(payload.get("tv_url")) or None,
        "tf": s(payload.get("tf")) or None,
        "currency": s(payload.get("currency")) or None,

        "price": to_float(payload.get("price")),
        "open": to_float(payload.get("open")),
        "high": to_float(payload.get("high")),
        "low": to_float(payload.get("low")),
        "prev_close": to_float(payload.get("prev_close")),
        "change_pct": to_float(payload.get("change_pct")),
        "volume": to_float(payload.get("volume")),
        "vol_ratio_1": to_float(payload.get("vol_ratio_1")),
        "vol_ratio_20": to_float(payload.get("vol_ratio_20")),
        "atr": to_float(payload.get("atr")),

        "ch_len": to_int(ch.get("len")),
        "width_type": s(ch.get("width_type")) or None,
        "ch_upper": to_float(ch.get("upper")),
        "ch_lower": to_float(ch.get("lower")),
        "ch_mid": to_float(ch.get("mid")),
        "ch_slope": to_float(ch.get("slope")),
        "width_pct": to_float(ch.get("width_pct")),
        "width_atr": to_float(ch.get("width_atr")),
        "pos_pct": to_float(ch.get("pos_pct")),

        "slope_atr": to_float(ft.get("slope_atr")),
        "eff_ratio": to_float(ft.get("eff_ratio")),
        "touch_up": to_int(ft.get("touch_up")),
        "touch_dn": to_int(ft.get("touch_dn")),
        "stability": to_float(ft.get("stability")),
        "bull_bar": to_bool_int(ft.get("bull_bar")),
        "upper_half": to_bool_int(ft.get("upper_half")),
        "gap_pct": to_float(ft.get("gap_pct")),
        "event_bar": to_bool_int(ft.get("event_bar")),
        "vol_spike": to_bool_int(ft.get("vol_spike")),

        "base_class": s(bs.get("class")) or None,
        "above_ma100": to_bool_int(bs.get("above_ma100")),
        "ma5": to_float(bs.get("ma5")), "ma10": to_float(bs.get("ma10")), "ma20": to_float(bs.get("ma20")),
        "ma50": to_float(bs.get("ma50")), "ma100": to_float(bs.get("ma100")),
        "ma20_chg5_pct": to_float(bs.get("ma20_chg5_pct")),
        "ma100_chg20_pct": to_float(bs.get("ma100_chg20_pct")),
        "w_ma5_chg1_pct": to_float(bs.get("w_ma5_chg1_pct")),
        "w_ma10_chg1_pct": to_float(bs.get("w_ma10_chg1_pct")),

        "gate_l1_flat": to_bool_int(gt.get("l1_flat")),
        "gate_l1_base": to_bool_int(gt.get("l1_base")),
        "gate_l2_width": to_bool_int(gt.get("l2_width")),
        "gate_l2_stab": to_bool_int(gt.get("l2_stab")),
        "gate_l2_touch_buy": to_bool_int(gt.get("l2_touch_buy")),
        "gate_l2_touch_sell": to_bool_int(gt.get("l2_touch_sell")),
        "confirm_buy": to_bool_int(gt.get("confirm_buy")),
        "confirm_sell": to_bool_int(gt.get("confirm_sell")),
        "can_fade": to_bool_int(gt.get("can_fade")),
        "fail_reason": s(gt.get("fail_reason")) or None,

        "c_fired": to_bool_int(sg.get("fired")) or 0,
        "c_side": s(sg.get("side")) or None,
        "c_kind": s(sg.get("kind")) or None,
        "break_up": to_bool_int(sg.get("break_up")),
        "break_down": to_bool_int(sg.get("break_down")),

        "a_fired": to_bool_int(a.get("fired")), "a_side": s(a.get("side")) or None, "a_pos_pct": to_float(a.get("pos_pct")),
        "a_upper": to_float(a.get("upper")), "a_lower": to_float(a.get("lower")),
        "b_fired": to_bool_int(b.get("fired")), "b_side": s(b.get("side")) or None, "b_pos_pct": to_float(b.get("pos_pct")),

        "round_up": to_float(ev.get("round_up")), "round_down": to_float(ev.get("round_down")),
        "body_high_20": to_float(ev.get("body_high_20")), "body_high_40": to_float(ev.get("body_high_40")), "body_high_60": to_float(ev.get("body_high_60")),
        "body_low_20": to_float(ev.get("body_low_20")), "body_low_40": to_float(ev.get("body_low_40")), "body_low_60": to_float(ev.get("body_low_60")),
        "vz1_low": to_float(vz1.get("low")), "vz1_high": to_float(vz1.get("high")), "vz1_ratio": to_float(vz1.get("ratio")),
        "vz2_low": to_float(vz2.get("low")), "vz2_high": to_float(vz2.get("high")), "vz2_ratio": to_float(vz2.get("ratio")),
        "vz3_low": to_float(vz3.get("low")), "vz3_high": to_float(vz3.get("high")), "vz3_ratio": to_float(vz3.get("ratio")),

        "payload_json": json.dumps(payload, ensure_ascii=False),
    }
    return row


def normalize_symbol(v: Any) -> str:
    text = s(v).strip().upper()
    if ":" in text:
        text = text.split(":")[-1]
    return text


SIGNAL_COLUMNS = [
    "signal_id", "received_at", "bar_date", "mode", "pine_version", "symbol", "name", "exchange", "tickerid", "tv_url", "tf", "currency",
    "price", "open", "high", "low", "prev_close", "change_pct", "volume", "vol_ratio_1", "vol_ratio_20", "atr",
    "ch_len", "width_type", "ch_upper", "ch_lower", "ch_mid", "ch_slope", "width_pct", "width_atr", "pos_pct",
    "slope_atr", "eff_ratio", "touch_up", "touch_dn", "stability", "bull_bar", "upper_half", "gap_pct", "event_bar", "vol_spike",
    "base_class", "above_ma100", "ma5", "ma10", "ma20", "ma50", "ma100", "ma20_chg5_pct", "ma100_chg20_pct", "w_ma5_chg1_pct", "w_ma10_chg1_pct",
    "gate_l1_flat", "gate_l1_base", "gate_l2_width", "gate_l2_stab", "gate_l2_touch_buy", "gate_l2_touch_sell", "confirm_buy", "confirm_sell", "can_fade", "fail_reason",
    "c_fired", "c_side", "c_kind", "break_up", "break_down",
    "a_fired", "a_side", "a_pos_pct", "a_upper", "a_lower", "b_fired", "b_side", "b_pos_pct",
    "round_up", "round_down", "body_high_20", "body_high_40", "body_high_60", "body_low_20", "body_low_40", "body_low_60",
    "vz1_low", "vz1_high", "vz1_ratio", "vz2_low", "vz2_high", "vz2_ratio", "vz3_low", "vz3_high", "vz3_ratio",
    "upper_reason", "lower_reason", "market", "sector",
    "verify_status", "verify_json", "quality_grade", "confidence", "confidence_score", "confidence_detail", "levels_json", "delivered", "delivered_at", "discord_thread_url", "ai_comment",
    "payload_json", "metrics_json", "cautions", "ai_model",
]


def insert_sql() -> str:
    cols = ", ".join(SIGNAL_COLUMNS)
    marks = ", ".join(["?"] * len(SIGNAL_COLUMNS))
    # 同じ signal_id が二度来ても一行のまま（配信状態や追跡を上書きしない）
    return "INSERT OR IGNORE INTO signals (%s) VALUES (%s)" % (cols, marks)


def row_values(row: Dict[str, Any]) -> List[Any]:
    return [row.get(c) for c in SIGNAL_COLUMNS]


# ---------------------------------------------------------------------------
# ウォッチリスト一括アラート（alertcondition）から来た軽い JSON を、
# 通常の形に膨らませる。文字の項目と、送っていない数値は None のままにし、
# あとの再計算（Yahoo の日足）で埋める。
# ---------------------------------------------------------------------------

_WL_CLASS = {1: "uptrend_pause", 2: "bottoming", 3: "directionless", 4: "downtrend"}
_WL_KIND = {1: "entry", 2: "deep", 3: "spring", 4: "pullback", 5: "outside"}


def _wl_date(v: Any) -> str:
    """{{time}} は 2026-09-11T06:00:00Z の形。日本時間の日付に直す。"""
    t = s(v).strip()
    if not t:
        return ""
    try:
        dt = datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return t[:10]
    return dt.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")


def is_watchlist(payload: Dict[str, Any]) -> bool:
    return s(payload.get("src")) == "wl" and isinstance(payload.get("wl"), dict)


def expand_watchlist(payload: Dict[str, Any]) -> Dict[str, Any]:
    """軽い JSON → 通常の JSON。src が wl でなければそのまま返す。"""
    if not is_watchlist(payload):
        return payload
    wl = payload.get("wl") or {}
    side = s(payload.get("side")).upper() or "LONG"
    brk = to_int(wl.get("brk")) or 0
    is_break = side == "BREAK" or brk > 0
    symbol = normalize_symbol(payload.get("symbol"))
    date_text = _wl_date(payload.get("bar_time"))
    price = to_float(payload.get("price"))
    upper = to_float(wl.get("upper"))
    lower = to_float(wl.get("lower"))
    mid = to_float(wl.get("mid"))
    pos = to_float(wl.get("pos"))
    if pos is None and None not in (upper, lower, price) and upper > lower:
        pos = (price - lower) / (upper - lower) * 100.0
    width_pct = _wl_scaled(wl.get("wpct_x10"), 10.0)
    width_atr = _wl_scaled(wl.get("watr_x10"), 10.0)
    stability = _wl_scaled(wl.get("stab_x100"), 100.0)
    slope_atr = _wl_scaled(wl.get("slope_x100"), 100.0)
    eff_ratio = _wl_scaled(wl.get("eff_x100"), 100.0)
    touch_up = to_int(wl.get("tup"))
    touch_dn = to_int(wl.get("tdn"))
    base_class = _WL_CLASS.get(to_int(wl.get("class")) or 0, "")
    kind = _WL_KIND.get(to_int(wl.get("kind")) or 0, "entry")
    open_ = to_float(payload.get("open"))

    return {
        "v": to_int(payload.get("v")) or config.EXPECTED_PAYLOAD_V,
        "src": "wl",
        "pine_version": s(payload.get("pine_version")),
        "mode": s(payload.get("mode")) or "signal",
        "signal_id": "%s_%s_%s" % (symbol, date_text.replace("-", ""),
                                   ("BREAK_UP" if brk == 1 else "BREAK_DOWN") if is_break else side),
        "symbol": symbol,
        "name": None,
        "exchange": None,
        "tickerid": s(payload.get("tickerid")) or None,
        "tv_url": "https://www.tradingview.com/chart/?symbol=" + (s(payload.get("tickerid")) or symbol),
        "tf": s(payload.get("tf")) or "1D",
        "currency": s(payload.get("currency")) or None,
        "bar_date": date_text,
        "price": price,
        "open": open_,
        "high": to_float(payload.get("high")),
        "low": to_float(payload.get("low")),
        "prev_close": None,
        "change_pct": None,
        "volume": to_float(payload.get("volume")),
        "vol_ratio_1": None,
        "vol_ratio_20": None,
        "atr": to_float(wl.get("atr")),
        "channel": {
            "len": to_int(wl.get("len")), "len_mode": "", "width_type": "",
            "upper": upper, "lower": lower, "mid": mid, "slope": None, "off": None,
            "width_pct": width_pct, "width_atr": width_atr, "pos_pct": pos,
        },
        "features": {
            "slope_atr": slope_atr, "eff_ratio": eff_ratio,
            "touch_up": touch_up, "touch_dn": touch_dn, "stability": stability,
            "bull_bar": (price is not None and open_ is not None and price > open_),
            "upper_half": None, "gap_pct": None, "event_bar": None, "vol_spike": None,
        },
        "base": {"class": base_class, "above_ma100": None},
        "gates": {
            "l1_flat": None, "l1_pull": None, "slope_label": "", "l1_base": None,
            "l2_width": None, "l2_stab": None, "l2_touch_buy": None, "l2_touch_sell": None,
            "confirm_buy": None, "confirm_sell": None, "can_fade": None,
            "fail_reason": "-", "waiting_buy": None, "waiting_sell": None,
        },
        "signal": {"method": "C", "fired": not is_break,
                   "side": "" if is_break else side, "kind": "" if is_break else kind,
                   "break_up": brk == 1, "break_down": brk == 2},
    }


def _wl_scaled(v: Any, div: float) -> Optional[float]:
    f = to_float(v)
    return None if f is None else f / div
