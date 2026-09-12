"""受信 → 検証 → 保存 → 再検証 → 画像 → 配信、の一本道。

順番が大事：
  1. まず保存（挿入だけ。300件が同時に来ても数秒）
  2. 方式 C が出た行だけ、再検証・画像・配信の重い処理へ
"""
from __future__ import annotations

import json
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from . import ai, cautions as cau, chart, config, discord, evidence, formatting, levels, market, payload as pl, verify, yahoo
from .db import Database
from .indicators import Bar

JST = config.JST


def now_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


async def process_raw(db: Database, raw_body: bytes, received_at: str) -> Dict[str, Any]:
    try:
        payload: Dict[str, Any] = json.loads(raw_body.decode("utf-8"))
        if not isinstance(payload, dict):
            payload = {"raw_body": raw_body.decode("utf-8", errors="replace")}
    except Exception:  # noqa: BLE001
        payload = {"raw_body": raw_body.decode("utf-8", errors="replace")}
    try:
        return await process_payload(db, payload, received_at)
    except Exception as exc:  # noqa: BLE001
        print("pipeline error: " + str(exc))
        traceback.print_exc()
        await _log_receive(db, received_at, str(payload.get("symbol") or ""), str(payload.get("signal_id") or ""), 0, str(exc)[:300])
        await discord.error("🚨 受信処理エラー %s %s\n%s" % (payload.get("symbol", "-"), payload.get("signal_id", "-"), str(exc)[:500]))
        return {"ok": False, "error": str(exc)}


async def process_payload(db: Database, payload: Dict[str, Any], received_at: str) -> Dict[str, Any]:
    # ウォッチリスト一括アラート（alertcondition）から来た軽い JSON は、先に通常の形に膨らませる
    payload = pl.expand_watchlist(payload)
    errors, warnings = pl.validate(payload)
    if errors:
        await _log_receive(db, received_at, str(payload.get("symbol") or ""), str(payload.get("signal_id") or ""), 0, "; ".join(errors)[:300])
        msg = "🚨 TradingView 受信の検証エラー\n%s %s / %s\n- %s" % (
            payload.get("symbol", "-"), payload.get("name", ""), payload.get("bar_date", "-"), "\n- ".join(errors[:8]))
        if warnings:
            msg += "\n注意:\n- " + "\n- ".join(warnings[:4])
        await discord.error(msg)
        return {"ok": False, "errors": errors}

    row = pl.flatten(payload, received_at)
    row["delivered"] = "none"
    await market.enrich(row, db)
    up_r, lo_r, up_s, lo_s = evidence.reasons_for_edges(row)
    row["upper_reason"] = _with_strength(up_r, up_s)
    row["lower_reason"] = _with_strength(lo_r, lo_s)
    row["quality_grade"] = grade(row)
    # 信頼度：いま引いている端の位置がどれだけ信用できるか。すべて加点で、引かない。
    # 品質（枠そのものの確かさ）とは別の軸なので、混ぜずに並べて出す。
    # 再計算との一致も加点に入るので、verify のあとで一度組み直す（下の方を参照）。
    conf = evidence.confidence(row, up_s, lo_s)
    row["confidence"], row["confidence_score"], row["confidence_detail"] = conf["label"], conf["score"], conf["detail"]

    # 枠の中の節目。参考情報であり、発火の判定には使わない
    zones = levels.build(row) if config.LEVELS_ENABLED else []
    row["levels"] = zones
    row["levels_json"] = json.dumps(zones, ensure_ascii=False) if zones else None
    row["metrics_json"] = json.dumps({"upper_score": up_s, "lower_score": lo_s, "warnings": warnings}, ensure_ascii=False)

    # 1. 先に保存（同じ signal_id は無視）
    before = await db.scalar("SELECT COUNT(*) AS c FROM signals WHERE signal_id = ?", [row["signal_id"]])
    await db.execute(pl.insert_sql(), pl.row_values(row))
    await _log_receive(db, received_at, row["symbol"], row["signal_id"], 1, None)
    if before:
        return {"ok": True, "duplicate": True, "signal_id": row["signal_id"]}

    # 再通知リセット（位置％が中心付近に戻った）
    pos = row.get("pos_pct")
    if pos is not None and config.RESET_POS_LOW <= pos <= config.RESET_POS_HIGH:
        await _arm_reset(db, row["symbol"])

    # 追跡行（A/B/C のどれかが出たら作る。方式比較のため）
    fired_any = row.get("c_fired") or row.get("a_fired") or row.get("b_fired")
    if fired_any and row.get("mode") != "preview":
        await ensure_tracking(db, row)

    if not row.get("c_fired"):
        return {"ok": True, "signal_id": row["signal_id"], "delivered": "none"}

    # 2. 方式 C が出た行だけ重い処理
    bars = await yahoo.fetch_daily_bars(row["symbol"], row.get("exchange") or "", row.get("tickerid") or "") if (config.VERIFY_ENABLED or config.CHART_IMAGE_ENABLED) else None
    bars_cut = yahoo.bars_up_to(bars, row["bar_date"]) if bars else []
    # 速報は「まだ確定していない今日の足」なので、Yahoo の確定値と突き合わせても必ずずれる。照合しない。
    if row.get("mode") == "preview":
        v = {"status": "skipped", "reason": "preview"}
    else:
        v = verify.verify_row(row, bars_cut) if (config.VERIFY_ENABLED and bars_cut) else {"status": "skipped", "reason": "no bars"}
    row["verify_status"] = v.get("status")
    row["verify_json"] = json.dumps(v, ensure_ascii=False)
    # 再計算の一致も加点要素なので、ここで信頼度を組み直す
    conf = evidence.confidence(row, up_s, lo_s)
    row["confidence"], row["confidence_score"], row["confidence_detail"] = conf["label"], conf["score"], conf["detail"]

    # 注意点（発火は止めない。通知と AI コメントに添えるだけ）
    row["cautions"] = cau.text(cau.build(row, bars_cut)) or None

    decision = await decide_delivery(db, row)
    row["suppressed_reason"] = decision.get("reason")
    thread_url: Optional[str] = None
    if decision["realtime"]:
        stats = None  # 銘柄ごとの過去成績の照合はしない。良し悪しは月次でまとめて見る
        # 画像を先に作る。AI にも同じ絵を見せるため
        attachment, png = None, None
        if config.CHART_IMAGE_ENABLED and bars_cut:
            try:
                png = chart.render_png(row, bars_cut)
                attachment = {"filename": "chart_%s_%s.png" % (row["symbol"], row["bar_date"].replace("-", "")), "content_type": "image/png", "data": png}
            except Exception as exc:  # noqa: BLE001
                print("chart failed: " + str(exc))
        if config.AI_ON_REALTIME:
            row["ai_comment"] = await ai.comment(row, stats, png, "realtime")
        is_preview = row.get("mode") == "preview"
        # 同じ日に速報を出していれば、確報は長文を繰り返さず、そのスレッドに一行だけ足す
        prev_thread = None if is_preview else await _preview_thread(db, row)
        if prev_thread:
            await discord.post_in_thread(config.DISCORD_WEBHOOK_URL_REALTIME, prev_thread,
                                         formatting.confirm_line(row))
            thread_url = prev_thread
        else:
            message = formatting.realtime_message(row, stats, provisional=is_preview)
            tname = formatting.thread_name(row, row["bar_date"]) if config.DISCORD_REALTIME_IS_FORUM else None
            thread_url = await discord.post(config.DISCORD_WEBHOOK_URL_REALTIME, message, tname, attachment)
        if not is_preview:
            await _mark_notified(db, row["symbol"], row.get("c_side") or "", row["bar_date"])
        row["delivered"] = "realtime"
        row["delivered_at"] = now_jst()

    await db.execute(
        "UPDATE signals SET verify_status = ?, verify_json = ?, delivered = ?, delivered_at = ?, discord_thread_url = ?, ai_comment = ?, "
        "cautions = ?, ai_model = ?, confidence = ?, confidence_score = ?, confidence_detail = ?, metrics_json = ? WHERE signal_id = ?",
        [row.get("verify_status"), row.get("verify_json"), row.get("delivered"), row.get("delivered_at"), thread_url, row.get("ai_comment"), row.get("cautions"), row.get("ai_model"),
         row.get("confidence"), row.get("confidence_score"), row.get("confidence_detail"),
         json.dumps({"suppressed_reason": decision.get("reason"), "grade": row.get("quality_grade")}, ensure_ascii=False), row["signal_id"]],
    )
    if v.get("status") == "mismatch":
        print("verify mismatch %s: %s" % (row["signal_id"], "; ".join(v.get("diffs", []))))
    return {"ok": True, "signal_id": row["signal_id"], "delivered": row["delivered"], "verify": v.get("status"), "reason": decision.get("reason")}


async def _preview_thread(db: Database, row: Dict[str, Any]) -> Optional[str]:
    """同じ銘柄・同じ日の速報が立てたスレッドの ID。無ければ None。"""
    r = await db.fetch_one(
        "SELECT discord_thread_url FROM signals WHERE symbol = ? AND bar_date = ? AND mode = 'preview' "
        "AND delivered = 'realtime' AND discord_thread_url IS NOT NULL ORDER BY received_at DESC LIMIT 1",
        [row["symbol"], row["bar_date"]])
    return discord.thread_id_from_url(r["discord_thread_url"]) if r else None


def grade(row: Dict[str, Any]) -> str:
    tu, td = int(row.get("touch_up") or 0), int(row.get("touch_dn") or 0)
    stab = float(row.get("stability") or 0.0)
    if (tu + td) >= config.GRADE_A_MIN_TOUCH_TOTAL and stab >= config.GRADE_A_MIN_STABILITY:
        return "A"
    return "B"


def _with_strength(text: str, score: float) -> str:
    if not text:
        return ""
    return "%s［%s］" % (text, evidence.strength_label(score))


async def decide_delivery(db: Database, row: Dict[str, Any]) -> Dict[str, Any]:
    """即時に流すか。流さない理由も返す（まとめ配信には出る）。"""
    if row.get("quality_grade") not in config.REALTIME_GRADES:
        return {"realtime": False, "reason": "品質" + str(row.get("quality_grade"))}
    if (row.get("base_class") or "") not in config.REALTIME_CLASSES:
        return {"realtime": False, "reason": "土台"}
    st = await db.fetch_one("SELECT last_notified_date, reset_armed FROM notify_state WHERE symbol = ? AND side = ?", [row["symbol"], row.get("c_side") or ""])
    if st and st.get("last_notified_date") and not st.get("reset_armed"):
        try:
            last = datetime.strptime(st["last_notified_date"], "%Y-%m-%d")
            cur = datetime.strptime(row["bar_date"], "%Y-%m-%d")
            if (cur - last).days < config.NOTIFY_COOLDOWN_DAYS:
                return {"realtime": False, "reason": "再通知抑制"}
        except ValueError:
            pass
    return {"realtime": True, "reason": None}


async def _arm_reset(db: Database, symbol: str) -> None:
    await db.execute("UPDATE notify_state SET reset_armed = 1, updated_at = ? WHERE symbol = ?", [now_jst(), symbol])


async def _mark_notified(db: Database, symbol: str, side: str, bar_date: str) -> None:
    await db.execute(
        "INSERT INTO notify_state (symbol, side, last_notified_date, reset_armed, updated_at) VALUES (?, ?, ?, 0, ?) "
        "ON CONFLICT(symbol, side) DO UPDATE SET last_notified_date = excluded.last_notified_date, reset_armed = 0, updated_at = excluded.updated_at",
        [symbol, side, bar_date, now_jst()],
    )


async def ensure_tracking(db: Database, row: Dict[str, Any]) -> None:
    side = row.get("c_side") or row.get("b_side") or row.get("a_side") or "LONG"
    exists = await db.scalar("SELECT COUNT(*) AS c FROM tracking WHERE signal_id = ?", [row["signal_id"]])
    if exists:
        return
    await db.execute(
        "INSERT INTO tracking (signal_id, entry_price, side, target_mid, target_upper, stop_lower, status, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
        [row["signal_id"], row.get("price"), side, row.get("ch_mid"), row.get("ch_upper") if side == "LONG" else row.get("ch_lower"),
         row.get("ch_lower") if side == "LONG" else row.get("ch_upper"), now_jst()],
    )


async def condition_stats(db: Database, row: Dict[str, Any]) -> Dict[str, Any]:
    """同じ土台・方向・種類で追跡が終わったものの実績。"""
    r = await db.fetch_one(
        "SELECT COUNT(*) AS n, "
        "AVG(CASE WHEN t.hit_target_day IS NOT NULL THEN 100.0 ELSE 0 END) AS reach_rate_pct, "
        "AVG(t.hit_target_day) AS avg_days, AVG(t.d10_ret_pct) AS avg_ret_10d "
        "FROM signals s JOIN tracking t ON t.signal_id = s.signal_id "
        "WHERE s.c_fired = 1 AND t.status = 'done' AND s.base_class = ? AND s.c_side = ? AND COALESCE(s.c_kind,'') = ?",
        [row.get("base_class"), row.get("c_side"), row.get("c_kind") or ""],
    )
    return dict(r) if r else {"n": 0}


async def _log_receive(db: Database, received_at: str, symbol: str, signal_id: str, ok: int, error: Optional[str]) -> None:
    try:
        await db.execute("INSERT INTO receive_log (received_at, symbol, signal_id, ok, error) VALUES (?, ?, ?, ?, ?)", [received_at, symbol, signal_id, ok, error])
    except Exception as exc:  # noqa: BLE001
        print("receive_log failed: " + str(exc))
