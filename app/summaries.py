"""まとめ配信：引け後まとめ・寄り前・月次。外部（GitHub Actions）から秘密の文字列付きで叩かれる。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from . import ai, config, discord, formatting, market, pipeline
from .db import Database

JST = config.JST


def today() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


async def already_sent(db: Database, kind: str, target: str) -> bool:
    return bool(await db.scalar("SELECT COUNT(*) AS c FROM summary_sent WHERE summary_type = ? AND target_date = ?", [kind, target]))


async def mark_sent(db: Database, kind: str, target: str, n: int) -> None:
    await db.execute("INSERT OR REPLACE INTO summary_sent (summary_type, target_date, sent_at, row_count) VALUES (?, ?, ?, ?)", [kind, target, pipeline.now_jst(), n])


def _class_rank(cls: str) -> int:
    return {"uptrend_pause": 0, "bottoming": 1, "downtrend": 2, "directionless": 3}.get(cls, 9)


async def close_digest(db: Database, date: Optional[str], force: bool) -> Dict[str, Any]:
    target = date or today()
    if await already_sent(db, "close_digest", target) and not force:
        return {"ok": True, "skipped": True, "reason": "already sent", "date": target}
    rows = [dict(r) for r in await db.execute("SELECT * FROM signals WHERE bar_date = ? AND c_fired = 1", [target])]
    for r in rows:
        try:
            r["suppressed_reason"] = (json.loads(r.get("metrics_json") or "{}") or {}).get("suppressed_reason")
        except Exception:  # noqa: BLE001
            r["suppressed_reason"] = None
    rows.sort(key=lambda r: (_class_rank(r.get("base_class") or ""), 0 if r.get("quality_grade") == "A" else 1, -(int(r.get("touch_up") or 0) + int(r.get("touch_dn") or 0))))
    breaks = [dict(r) for r in await db.execute("SELECT * FROM signals WHERE bar_date = ? AND (break_up = 1 OR break_down = 1) AND mode = 'signal'", [target])]
    counts = await db.fetch_one(
        "SELECT COUNT(*) AS received, SUM(a_fired) AS a_n, SUM(b_fired) AS b_n, SUM(c_fired) AS c_n FROM signals WHERE bar_date = ?", [target])
    stats_line = "受信 %s件｜発火 A %s / B %s / C %s" % (counts.get("received"), counts.get("a_n") or 0, counts.get("b_n") or 0, counts.get("c_n") or 0) if counts else ""
    ai_lines: Dict[str, str] = {}
    for r in rows[: config.AI_TOP_N]:
        if r.get("ai_comment"):
            ai_lines[str(r["signal_id"])] = str(r["ai_comment"])
            continue
        stats = await pipeline.condition_stats(db, r)
        text = await ai.comment(r, stats, None, "digest")
        ai_lines[str(r["signal_id"])] = text
        await db.execute("UPDATE signals SET ai_comment = ? WHERE signal_id = ?", [text, r["signal_id"]])
    await _notify_lapsed(db, target)
    message = formatting.close_digest(target, rows, breaks, stats_line, ai_lines, "")
    tname = "引け後まとめ %s 候補%d件" % (target[5:], len(rows)) if config.DISCORD_DIGEST_IS_FORUM else None
    url = await discord.post(config.DISCORD_WEBHOOK_URL_DIGEST, message, tname)
    for r in rows:
        new_state = "both" if r.get("delivered") in ("realtime", "both") else "digest"
        await db.execute("UPDATE signals SET delivered = ? WHERE signal_id = ?", [new_state, r["signal_id"]])
    await mark_sent(db, "close_digest", target, len(rows))
    return {"ok": True, "date": target, "count": len(rows), "breaks": len(breaks), "url": url}


async def _notify_lapsed(db: Database, target: str) -> int:
    """速報は出したのに、引けでは発火しなかった銘柄のスレッドに一行足す。

    速報は未確定の足を見ているので、引けまでに形が変わることがある。
    黙って終わると「買ってよかったのか」が分からなくなるので、必ず結末を書く。
    """
    rows = await db.execute(
        "SELECT * FROM signals WHERE bar_date = ? AND mode = 'preview' AND delivered = 'realtime' "
        "AND discord_thread_url IS NOT NULL", [target])
    sent = 0
    for r in rows:
        r = dict(r)
        ok = await db.scalar(
            "SELECT COUNT(*) AS c FROM signals WHERE symbol = ? AND bar_date = ? AND mode = 'signal' AND c_fired = 1",
            [r["symbol"], target])
        if ok:
            continue
        tid = discord.thread_id_from_url(r.get("discord_thread_url"))
        if not tid:
            continue
        await discord.post_in_thread(config.DISCORD_WEBHOOK_URL_REALTIME, tid, formatting.lapsed_line(r))
        sent += 1
    return sent


async def morning(db: Database, date: Optional[str], force: bool) -> Dict[str, Any]:
    target = date or today()
    if await already_sent(db, "morning", target) and not force:
        return {"ok": True, "skipped": True, "reason": "already sent", "date": target}
    # 今日の寄りで手を出す候補＝前営業日に実際に通知したもの
    prev = await db.scalar("SELECT MAX(bar_date) AS d FROM signals WHERE bar_date < ? AND delivered = 'realtime'", [target])
    rows = [dict(r) for r in await db.execute(
        "SELECT * FROM signals WHERE bar_date = ? AND delivered = 'realtime'", [prev])] if prev else []
    rows.sort(key=lambda r: (_class_rank(r.get("base_class") or ""), 0 if r.get("quality_grade") == "A" else 1))
    # 出口が近い保有中のもの（中心まで残り2％以内、または端の外に出ている）
    exits = [dict(r) for r in await db.execute(
        "SELECT s.symbol, s.name, s.c_side, s.bar_date, s.price, s.ch_mid, s.ch_upper, s.ch_lower, "
        "t.d1_close, t.days_tracked, t.hit_mid_day, t.broke_stop_day "
        "FROM tracking t JOIN signals s ON s.signal_id = t.signal_id "
        "WHERE t.status = 'open' AND s.delivered = 'realtime' ORDER BY s.bar_date")]
    message = formatting.morning_message(target, rows, exits)
    tname = "寄り前 %s 候補%d件" % (target[5:], len(rows)) if config.DISCORD_DIGEST_IS_FORUM else None
    url = await discord.post(config.DISCORD_WEBHOOK_URL_DIGEST, message, tname)
    await mark_sent(db, "morning", target, len(rows))
    return {"ok": True, "date": target, "count": len(rows), "from": prev, "url": url}


async def monthly(db: Database, month: Optional[str], force: bool) -> Dict[str, Any]:
    """month: YYYY-MM。省略時は前月。"""
    if not month:
        first = datetime.now(JST).replace(day=1)
        month = (first - timedelta(days=1)).strftime("%Y-%m")
    if await already_sent(db, "monthly", month) and not force:
        return {"ok": True, "skipped": True, "reason": "already sent", "month": month}
    table = [dict(r) for r in await db.execute(
        "SELECT s.base_class, s.c_kind, COUNT(*) AS n, "
        "AVG(CASE WHEN t.hit_target_day IS NOT NULL THEN 100.0 ELSE 0 END) AS reach_rate_pct, "
        "AVG(t.hit_target_day) AS avg_days, AVG(t.d10_ret_pct) AS avg_ret_10d, AVG(t.max_dn_pct_20) AS avg_dd "
        "FROM signals s JOIN tracking t ON t.signal_id = s.signal_id "
        "WHERE s.c_fired = 1 AND t.status = 'done' AND substr(s.bar_date, 1, 7) = ? "
        "GROUP BY s.base_class, s.c_kind ORDER BY s.base_class, s.c_kind", [month])]
    totals = await db.fetch_one(
        "SELECT COUNT(*) AS n, SUM(a_fired) AS a_n, SUM(b_fired) AS b_n, SUM(c_fired) AS c_n FROM signals s "
        "WHERE substr(s.bar_date, 1, 7) = ?", [month]) or {}
    message = formatting.monthly_message(month, table, totals)
    tname = "月次集計 %s" % month if config.DISCORD_DIGEST_IS_FORUM else None
    url = await discord.post(config.DISCORD_WEBHOOK_URL_DIGEST, message, tname)
    await mark_sent(db, "monthly", month, len(table))
    return {"ok": True, "month": month, "rows": len(table), "url": url}


async def receive_check(db: Database, date: Optional[str], expected: int) -> Dict[str, Any]:
    """15:45 に呼ぶ。届いた件数を数え、期待より少なければエラー用チャンネルへ。"""
    target = date or today()
    n = await db.scalar("SELECT COUNT(*) AS c FROM receive_log WHERE substr(received_at, 1, 10) = ? AND ok = 1", [target]) or 0
    n_err = await db.scalar("SELECT COUNT(*) AS c FROM receive_log WHERE substr(received_at, 1, 10) = ? AND ok = 0", [target]) or 0
    msg = None
    if expected and n < expected:
        msg = "⚠ 受信件数が少ない %s：%d / 期待 %d（エラー %d）。Render のスリープや TradingView のアラート期限を確認。" % (target, n, expected, n_err)
        await discord.error(msg)
    elif n == 0:
        msg = "⚠ 本日の受信が 0 件です（%s）。相場が動かなかったのか、止まっているのか確認。" % target
        await discord.error(msg)
    return {"ok": True, "date": target, "received": n, "errors": n_err, "warning": msg}
