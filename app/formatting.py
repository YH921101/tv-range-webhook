"""Discord に流す文面。要件定義 §8-1 の形。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import levels

CLASS_JP = {"uptrend_pause": "上昇中のヨコヨコ", "bottoming": "底値圏", "directionless": "方向感なし", "downtrend": "下落中"}
KIND_JP = {"entry": "到達", "deep": "深追い", "spring": "復帰", "pullback": "押し目", "outside": "端の外"}
SIDE_JP = {"LONG": "下端 買い", "SHORT": "上端 売り"}


def f(v: Any, digits: int = 1, sign: bool = False) -> str:
    if v is None:
        return "-"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = ("%+." if sign else "%.") + str(digits) + "f"
    return s % x


def unit(currency: Optional[str] = None) -> str:
    return "円" if (currency or "JPY").upper() in ("JPY", "") else ""


def yen(v: Any, currency: Optional[str] = None) -> str:
    """金額。円は整数、それ以外（米ドルなど）は小数2桁で通貨記号を付ける。"""
    if v is None:
        return "-"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    cur = (currency or "JPY").upper()
    if cur in ("JPY", ""):
        return "{:,.0f}".format(x)
    mark = {"USD": "$", "EUR": "€", "GBP": "£", "HKD": "HK$"}.get(cur, cur + " ")
    return mark + "{:,.2f}".format(x)


def pct_to(target: Optional[float], price: Optional[float]) -> str:
    if not target or not price:
        return "-"
    return f((target / price - 1.0) * 100.0, 1, sign=True) + "%"


def display_name(row: Dict[str, Any]) -> str:
    return "%s %s" % (row.get("symbol") or "", row.get("name") or "")


def head_line(row: Dict[str, Any], prefix: str = "📐") -> str:
    cls = CLASS_JP.get(row.get("base_class") or "", "-")
    side = SIDE_JP.get(row.get("c_side") or "", row.get("c_side") or "-")
    kind = KIND_JP.get(row.get("c_kind") or "", "")
    grade = row.get("quality_grade") or "-"
    conf = row.get("confidence") or "-"
    sc = row.get("confidence_score")
    conf_text = conf + ("" if sc is None else " %s点" % f(sc, 0))
    return "%s %s｜日足 %s%s｜%s｜品質 %s（反応 上%s / 下%s）｜信頼度 %s" % (
        prefix, display_name(row), side, ("・" + kind if kind and kind != "到達" else ""), cls, grade,
        row.get("touch_up"), row.get("touch_dn"), conf_text)


def realtime_message(row: Dict[str, Any], stats: Optional[Dict[str, Any]] = None, provisional: bool = False) -> str:
    price = row.get("price")
    _y = lambda v: yen(v, row.get("currency"))
    lines = [head_line(row, "⚡ 速報（未確定）" if provisional else "📐")]
    lines.append("💹 %s%s（%s%%）｜位置 %s%%｜幅 %s ATR（%s%%）｜傾き %s ATR/日｜安定度 %s" % (
        _y(price), unit(row.get("currency")), f(row.get("change_pct"), 1, True), f(row.get("pos_pct"), 0), f(row.get("width_atr"), 1), f(row.get("width_pct"), 1),
        f(row.get("slope_atr"), 2), f(row.get("stability"), 2)))
    lines.append("📦 レンジ %s – %s（%s日）　上端の根拠：%s　下端の根拠：%s" % (
        _y(row.get("ch_lower")), _y(row.get("ch_upper")), row.get("ch_len"), row.get("upper_reason") or "-", row.get("lower_reason") or "-"))
    if (row.get("c_side") or "") == "SHORT":
        lines.append("🎯 中心 %s（%s）／下端 %s（%s）　🛑 上端抜け %s（%s）" % (
            _y(row.get("ch_mid")), pct_to(row.get("ch_mid"), price), _y(row.get("ch_lower")), pct_to(row.get("ch_lower"), price),
            _y(row.get("ch_upper")), pct_to(row.get("ch_upper"), price)))
    else:
        lines.append("🎯 中心 %s（%s）／上端 %s（%s）　🛑 下端割れ %s（%s）" % (
            _y(row.get("ch_mid")), pct_to(row.get("ch_mid"), price), _y(row.get("ch_upper")), pct_to(row.get("ch_upper"), price),
            _y(row.get("ch_lower")), pct_to(row.get("ch_lower"), price)))
    if row.get("confidence_detail"):
        lines.append("🔎 信頼度の内訳：" + str(row["confidence_detail"]))
    zones = row.get("levels") or []
    if zones:
        lines.append("📍 目印が重なっている価格帯（枠の中／抜けた先／割れた先）")
        lines.extend(levels.text_lines(zones, _y))
    lines.append("📊 出来高 前日比 %s倍 / 20日比 %s倍｜%s" % (
        f(row.get("vol_ratio_1"), 1), f(row.get("vol_ratio_20"), 1), " / ".join(x for x in (row.get("market"), row.get("sector")) if x) or "-"))
    if stats and stats.get("n", 0) >= 10:
        lines.append("🧭 同条件の実績：%d件 → 反対側の端に到達 %s%%、平均 %s日" % (stats["n"], f(stats.get("reach_rate_pct"), 0), f(stats.get("avg_days"), 1)))
    if row.get("cautions"):
        lines.append("⚠ 注意")
        for n in str(row["cautions"]).split(" / "):
            lines.append("　・" + n)
    elif row.get("verify_status") == "mismatch":
        lines.append("⚠ Render 側の再計算と食い違いあり（記録済み）")
    if row.get("ai_comment"):
        lines.append("")
        lines.append("📝 " + str(row["ai_comment"]).replace("\n", "\n"))
    if row.get("tv_url"):
        lines.append("🔗 " + str(row["tv_url"]))
    return "\n".join(lines)


def thread_name(row: Dict[str, Any], date_text: str) -> str:
    side = "買" if (row.get("c_side") or "") == "LONG" else "売"
    kind = KIND_JP.get(row.get("c_kind") or "", "")
    cls = CLASS_JP.get(row.get("base_class") or "", "")
    return "%s %s %s%s %s 品質%s" % (date_text[5:], display_name(row), side, ("・" + kind if kind in ("復帰", "押し目", "端の外") else ""), cls, row.get("quality_grade") or "-")


def digest_row(row: Dict[str, Any], idx: int) -> str:
    _y = lambda v: yen(v, (row or {}).get("currency"))
    side = "買" if (row.get("c_side") or "") == "LONG" else "売"
    kind = KIND_JP.get(row.get("c_kind") or "", "")
    flags = []
    if row.get("delivered") in ("realtime", "both"):
        flags.append("即時済")
    if row.get("verify_status") == "mismatch":
        flags.append("再計算差")
    if row.get("suppressed_reason"):
        flags.append(str(row["suppressed_reason"]))
    return "%d. %s｜%s%s｜品質%s 反応%s/%s 幅%s%% 安定%s｜位置%s%%｜%s → 中心%s 上端%s 下端%s%s" % (
        idx, display_name(row), side, ("・" + kind if kind in ("復帰", "押し目", "深追い", "端の外") else ""), row.get("quality_grade") or "-",
        row.get("touch_up"), row.get("touch_dn"), f(row.get("width_pct"), 1), f(row.get("stability"), 2), f(row.get("pos_pct"), 0),
        _y(row.get("price")), _y(row.get("ch_mid")), _y(row.get("ch_upper")), _y(row.get("ch_lower")),
        ("（" + "・".join(flags) + "）") if flags else "")


def close_digest(date_text: str, rows: List[Dict[str, Any]], breaks: List[Dict[str, Any]], stats_line: str, ai_lines: Dict[str, str], market_text: str) -> str:
    lines = ["📋 引け後まとめ %s｜候補 %d件%s" % (date_text, len(rows), ("｜崩れ %d件" % len(breaks)) if breaks else "")]
    if market_text:
        lines.append("🌤 地合い：" + market_text)
    if stats_line:
        lines.append(stats_line)
    lines.append("")
    if not rows:
        lines.append("本日は方式Cの通過なし。")
    order = ["uptrend_pause", "bottoming", "downtrend", "directionless"]
    idx = 0
    for cls in order:
        group = [r for r in rows if (r.get("base_class") or "") == cls]
        if not group:
            continue
        lines.append("■ " + CLASS_JP[cls])
        for r in group:
            idx += 1
            lines.append(digest_row(r, idx))
            c = ai_lines.get(str(r.get("signal_id")))
            if c:
                lines.extend(["　　" + ln for ln in c.splitlines()])
            if r.get("tv_url"):
                lines.append("　　🔗 " + str(r["tv_url"]))
        lines.append("")
    if breaks:
        lines.append("■ 崩れ（監視から外す）")
        for r in breaks:
            lines.append("・%s %s %s%s（%s%%）出来高 20日比 %s倍" % (
                display_name(r), "上抜け" if r.get("break_up") else "下抜け", yen2(r, r.get("price")), unit(r.get("currency")),
                f(r.get("change_pct"), 1, True), f(r.get("vol_ratio_20"), 1)))
    return "\n".join(lines).rstrip()


def yen2(row: Dict[str, Any], v: Any) -> str:
    return yen(v, (row or {}).get("currency"))


def morning_message(date_text: str, rows: List[Dict[str, Any]], holding: Optional[List[Dict[str, Any]]] = None) -> str:
    """朝、注文を出す前に見るもの。前日の候補と、持っている銘柄の出口だけ。"""
    holding = holding or []
    lines = ["🌅 寄り前 %s｜前日の候補 %d件／追跡中 %d件" % (date_text, len(rows), len(holding)), ""]
    if not rows:
        lines.append("前日の候補なし。")
    for i, r in enumerate(rows, 1):
        side = "買" if (r.get("c_side") or "") == "LONG" else "売"
        kind = KIND_JP.get(r.get("c_kind") or "", "")
        lines.append("%d. %s｜%s%s｜%s円｜中心 %s（%s）／端 %s（%s）／撤退 %s（%s）" % (
            i, display_name(r), side, ("・" + kind if kind and kind != "到達" else ""), yen2(r, r.get("price")),
            yen2(r, r.get("ch_mid")), pct_to(r.get("ch_mid"), r.get("price")),
            yen2(r, r.get("ch_upper")), pct_to(r.get("ch_upper"), r.get("price")),
            yen2(r, r.get("ch_lower")), pct_to(r.get("ch_lower"), r.get("price"))))
        if r.get("tv_url"):
            lines.append("　　🔗 " + str(r["tv_url"]))
    if holding:
        lines.append("")
        lines.append("📌 追いかけ中（出口の目安）")
        for h in holding:
            now = h.get("d1_close") or h.get("price")
            lines.append("　・%s｜%s日目｜現値 %s円｜中心 %s（%s）／撤退 %s（%s）" % (
                display_name(h), h.get("days_tracked") or 0, yen2(h, now),
                yen2(h, h.get("ch_mid")), pct_to(h.get("ch_mid"), now),
                yen2(h, h.get("ch_lower")), pct_to(h.get("ch_lower"), now)))
    lines.append("")
    lines.append("成行なら 9:00 寄り付き、または 15:00〜15:30。中心まで戻ったら半分、端の外で3本続けたら撤退。")
    return "\n".join(lines)


def monthly_message(month_text: str, table: List[Dict[str, Any]], totals: Dict[str, Any]) -> str:
    lines = ["📈 月次集計 %s｜対象 %d件（追跡完了分）" % (month_text, int(totals.get("n") or 0))]
    lines.append("")
    lines.append("区分｜件数｜到達率｜平均日数｜10日後平均｜最大逆行平均")
    for t in table:
        lines.append("%s %s｜%d｜%s%%｜%s日｜%s%%｜%s%%" % (
            CLASS_JP.get(t.get("base_class") or "", t.get("base_class") or "-"), KIND_JP.get(t.get("c_kind") or "", t.get("c_kind") or ""),
            int(t.get("n") or 0), f(t.get("reach_rate_pct"), 0), f(t.get("avg_days"), 1), f(t.get("avg_ret_10d"), 1, True), f(t.get("avg_dd"), 1)))
    lines.append("")
    lines.append("方式別（記録のみ含む）：A %s件 / B %s件 / C %s件" % (totals.get("a_n", "-"), totals.get("b_n", "-"), totals.get("c_n", "-")))
    return "\n".join(lines)


EXIT_JP = {
    "mid": ("🎯", "中心まで戻った", "半分利食いの目安"),
    "target": ("🏁", "反対側の端に到達", "利食いの目安"),
    "stop": ("🛑", "端の外で3本続けて引けた", "撤退の目安"),
}


def exit_line(row: Dict[str, Any], event: str, day: int, price: Optional[float], ret_pct: Optional[float]) -> str:
    _y = lambda v: yen(v, (row or {}).get("currency"))
    """手仕舞いの合図。保有中に見たい一行なので、短く。"""
    icon, what, advice = EXIT_JP.get(event, ("•", event, ""))
    side = "買い" if (row.get("side") or "LONG") != "SHORT" else "売り"
    return "%s %s %s（%s）｜%s %s%s %s%%｜発生から%d営業日｜%s" % (
        icon, row.get("symbol"), row.get("name") or "", side, what, _y(price), unit((row or {}).get("currency")),
        f(ret_pct, 1, True), day, advice)


def confirm_line(row: Dict[str, Any]) -> str:
    """速報を出した銘柄が、引けでも条件を満たしたときの一行。長文は繰り返さない。"""
    _y = lambda v: yen(v, row.get("currency"))
    return "✅ 引けでも条件を満たした｜終値 %s%s（%s%%）｜位置 %s%%｜下端 %s／中心 %s" % (
        _y(row.get("price")), unit(row.get("currency")), f(row.get("change_pct"), 1, True),
        f(row.get("pos_pct"), 0), _y(row.get("ch_lower")), _y(row.get("ch_mid")))


def lapsed_line(row: Dict[str, Any], price: Any = None) -> str:
    """速報は出たが、引けでは条件を外れたときの一行。"""
    _y = lambda v: yen(v, row.get("currency"))
    p = price if price is not None else row.get("price")
    return "↩️ 引けでは条件を外れた｜終値 %s%s｜速報どおりに買っていたら、翌日の動きは慎重に見たい" % (
        _y(p), unit(row.get("currency")))
