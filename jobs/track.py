"""追跡の更新。GitHub Actions から `python -m jobs.track` で動かす（Render を経由しない）。

open の追跡行ごとに Yahoo の日足を取り、発生日より後の値動きを埋める。
  ・1/3/5/10/20 営業日後の終値と損益率
  ・20営業日内の最大順行・最大逆行
  ・中心・反対側の端に何日で到達したか、下端の外で3本続けて引けた日が何日目か
  ・最初に起きた事象と結果（win / loss / flat / open）
20営業日ぶん埋まったら done。
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, discord, formatting, yahoo  # noqa: E402
from app.db import Database, get_db, schema_sql  # noqa: E402
from app.indicators import Bar  # noqa: E402

HORIZON = 20
STOP_BARS = int(os.getenv("STOP_BARS", "3"))   # 枠の外で何本続けて引けたら撤退とみなすか（Pine の崩れと同じ本数）


def compute_tracking(side: str, entry: float, target_mid: Optional[float], target: Optional[float], stop: Optional[float], post: List[Bar]) -> Dict[str, Any]:
    """発生日より後の足 post（古い→新しい）から追跡の数字を作る。純粋関数（テスト用）。"""
    out: Dict[str, Any] = {}
    long = side != "SHORT"
    fav = lambda b: (b.high / entry - 1.0) * 100.0 if long else (entry / b.low - 1.0) * 100.0  # noqa: E731
    adv = lambda b: (b.low / entry - 1.0) * 100.0 if long else (entry / b.high - 1.0) * 100.0  # noqa: E731
    ret = lambda c: (c / entry - 1.0) * 100.0 if long else (entry / c - 1.0) * 100.0  # noqa: E731
    window = post[:HORIZON]
    for n in (1, 3, 5, 10, 20):
        if len(post) >= n:
            out["d%d_close" % n] = post[n - 1].close
            out["d%d_ret_pct" % n] = ret(post[n - 1].close)
    if window:
        out["max_up_pct_20"] = max(fav(b) for b in window)
        out["max_dn_pct_20"] = min(adv(b) for b in window)
    hit_mid = hit_target = broke = None
    below_streak = 0
    for i, b in enumerate(window, 1):
        if target_mid and hit_mid is None and ((long and b.high >= target_mid) or (not long and b.low <= target_mid)):
            hit_mid = i
        if target and hit_target is None and ((long and b.high >= target) or (not long and b.low <= target)):
            hit_target = i
        if stop:
            broken_bar = (long and b.close < stop) or (not long and b.close > stop)
            below_streak = below_streak + 1 if broken_bar else 0
            if below_streak >= STOP_BARS and broke is None:
                broke = i
    out["hit_mid_day"] = hit_mid
    out["hit_target_day"] = hit_target
    out["broke_stop_day"] = broke
    events: List[Tuple[int, str]] = []
    if hit_target is not None:
        events.append((hit_target, "target"))
    if broke is not None:
        events.append((broke, "stop"))
    if hit_mid is not None:
        events.append((hit_mid, "mid"))
    # 同じ日なら stop を優先（保守的）
    order = {"stop": 0, "target": 1, "mid": 2}
    events.sort(key=lambda e: (e[0], order[e[1]]))
    out["first_event"] = events[0][1] if events else ("none" if len(window) >= HORIZON else None)
    if hit_target is not None and (broke is None or hit_target <= broke):
        out["outcome"] = "win"
    elif broke is not None:
        out["outcome"] = "loss"
    elif len(window) >= HORIZON:
        out["outcome"] = "flat"
    else:
        out["outcome"] = "open"
    out["days_tracked"] = min(len(post), HORIZON)
    out["status"] = "done" if len(post) >= HORIZON else "open"
    out["last_tracked_date"] = post[-1].date if post else None
    return out


async def run(limit: int = 500) -> Dict[str, Any]:
    db: Database = get_db()
    await db.init_schema(schema_sql())
    rows = await db.execute(
        "SELECT t.signal_id, t.side, t.entry_price, t.target_mid, t.target_upper, t.stop_lower, t.notified_exits, "
        "s.symbol, s.name, s.exchange, s.tickerid, s.bar_date, s.delivered, s.discord_thread_url "
        "FROM tracking t JOIN signals s ON s.signal_id = t.signal_id WHERE t.status = 'open' ORDER BY s.bar_date LIMIT ?", [limit])
    updated = done = failed = exits = 0
    cache: Dict[str, Optional[List[Bar]]] = {}
    for r in rows:
        key = r["symbol"]
        if key not in cache:
            cache[key] = await yahoo.fetch_daily_bars(r["symbol"], r.get("exchange") or "", r.get("tickerid") or "", rng="6mo")
            await asyncio.sleep(0.25)
        bars = cache[key]
        if not bars:
            failed += 1
            continue
        post = [b for b in bars if b.date > r["bar_date"]]
        if not post:
            continue
        res = compute_tracking(r["side"], float(r["entry_price"]), r.get("target_mid"), r.get("target_upper"), r.get("stop_lower"), post)
        cols = ["d1_close", "d3_close", "d5_close", "d10_close", "d20_close", "d1_ret_pct", "d3_ret_pct", "d5_ret_pct", "d10_ret_pct", "d20_ret_pct",
                "max_up_pct_20", "max_dn_pct_20", "hit_mid_day", "hit_target_day", "broke_stop_day", "first_event", "outcome", "days_tracked", "status", "last_tracked_date"]
        sets = ", ".join(c + " = ?" for c in cols)
        await db.execute("UPDATE tracking SET %s, updated_at = ? WHERE signal_id = ?" % sets,
                         [res.get(c) for c in cols] + [datetime.now(config.JST).isoformat(timespec="seconds"), r["signal_id"]])
        updated += 1
        if res["status"] == "done":
            done += 1
        sent = await notify_exits(db, r, res, post)
        exits += sent
    await db.close()
    summary = {"open_rows": len(rows), "updated": updated, "done": done, "fetch_failed": failed, "exit_alerts": exits}
    print(summary)
    return summary


async def notify_exits(db: Database, r: Dict[str, Any], res: Dict[str, Any], post: List[Bar]) -> int:
    """中心到達・端到達・撤退を、起きた順に一度だけ知らせる。

    保有しているあいだに見たいのはこの三つだけなので、それ以外は黙っている。
    """
    # 出口を知らせるのは「買い場として実際に通知したもの」だけ。
    # 記録用に追いかけている方式A/Bや、品質で見送ったものまで鳴らすと意味がなくなる。
    if (r.get("delivered") or "none") != "realtime":
        return 0
    # 出口は、その銘柄の通知が立てたスレッドに追記する（入りから出口まで1本にまとまる）
    thread_id = discord.thread_id_from_url(r.get("discord_thread_url"))
    url = config.DISCORD_WEBHOOK_URL_EXIT or config.DISCORD_WEBHOOK_URL_REALTIME
    if not url or not thread_id:
        return 0
    already = set(x for x in str(r.get("notified_exits") or "").split(",") if x)
    entry = float(r["entry_price"])
    long = (r.get("side") or "LONG") != "SHORT"
    pairs = [("mid", res.get("hit_mid_day")), ("target", res.get("hit_target_day")), ("stop", res.get("broke_stop_day"))]
    fresh = sorted([(d, ev) for ev, d in pairs if d and ev not in already])
    if not fresh:
        return 0
    sent = 0
    for day, ev in fresh:
        close = post[day - 1].close if len(post) >= day else None
        ret = ((close / entry - 1.0) if long else (entry / close - 1.0)) * 100.0 if close else None
        await discord.post_in_thread(url, thread_id, formatting.exit_line(r, ev, day, close, ret))
        already.add(ev)
        sent += 1
    await db.execute("UPDATE tracking SET notified_exits = ? WHERE signal_id = ?",
                     [",".join(sorted(already)), r["signal_id"]])
    return sent


if __name__ == "__main__":
    asyncio.run(run())
