"""TradingView → Render → Discord（レンジ通知 v1）

Starlette で書いてある（FastAPI の土台。依存が少なく、起動が速い）。
起動: uvicorn main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import csv
import io
import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app import config, discord, market, pipeline, summaries
from app.db import Database, get_db, schema_sql

JST = config.JST


def _secret_ok(request: Request) -> bool:
    return bool(config.SUMMARY_SECRET) and request.query_params.get("secret", "") == config.SUMMARY_SECRET


# 起動時の結果を残しておき、/ で見えるようにする。
# ここで例外を投げてしまうと Render は「Exited with status 1」だけを残して落ち、
# / も開けなくなって原因が分からない。落とさずに起動し、理由を画面に出す。
STARTUP: Dict[str, Any] = {}


async def startup() -> None:
    db = get_db()
    STARTUP["db"] = db.kind
    STARTUP["turso_url_prefix"] = (config.TURSO_DATABASE_URL.split("://")[0] + "://") if "://" in config.TURSO_DATABASE_URL else "(未設定)"
    STARTUP["turso_token"] = "あり" if config.TURSO_AUTH_TOKEN else "なし"
    try:
        await db.init_schema(schema_sql())
        STARTUP["schema"] = "ok"
        STARTUP["error"] = None
    except Exception as exc:  # noqa: BLE001
        STARTUP["schema"] = "failed"
        STARTUP["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:400])
        print("startup: init_schema failed -> " + STARTUP["error"])
    try:
        await market.load_stock_master(force=True)
        STARTUP["stock_master"] = "ok"
    except Exception as exc:  # noqa: BLE001
        STARTUP["stock_master"] = "failed: " + str(exc)[:200]
        print("startup: stock master failed -> " + str(exc))
    print("startup done: db=%s schema=%s version=%s" % (db.kind, STARTUP.get("schema"), config.APP_VERSION))


async def health(request: Request) -> JSONResponse:
    db = get_db()
    try:
        n = await db.scalar("SELECT COUNT(*) AS c FROM signals")
    except Exception as exc:  # noqa: BLE001
        n = "error: " + str(exc)[:80]
    return JSONResponse({
        "status": "ok" if STARTUP.get("schema") == "ok" else "起動時に問題あり",
        "startup": STARTUP,
        "version": config.APP_VERSION, "db": db.kind, "signals": n,
        "expected_pine_version": config.EXPECTED_PINE_VERSION,
        "discord_realtime": bool(config.DISCORD_WEBHOOK_URL_REALTIME), "discord_digest": bool(config.DISCORD_WEBHOOK_URL_DIGEST),
        "discord_error": bool(config.DISCORD_WEBHOOK_URL_ERROR), "openai": bool(config.OPENAI_API_KEY),
        "time": datetime.now(JST).isoformat(timespec="seconds"),
    })


async def webhook_tradingview(request: Request) -> JSONResponse:
    """先に 200 を返し、処理は裏に回す（TradingView の待ち時間切れを避ける）。"""
    received_at = datetime.now(JST).isoformat(timespec="seconds")
    try:
        raw = await request.body()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": True, "queued": False, "error": "body read failed: " + str(exc)})
    task = BackgroundTask(pipeline.process_raw, get_db(), raw, received_at)
    return JSONResponse({"ok": True, "queued": True, "received_at": received_at}, background=task)


async def signals_recent(request: Request) -> JSONResponse:
    limit = min(max(int(request.query_params.get("limit", "20")), 1), 200)
    rows = await get_db().execute(
        "SELECT signal_id, bar_date, symbol, name, base_class, quality_grade, c_fired, c_side, c_kind, pos_pct, touch_up, touch_dn, "
        "width_pct, stability, delivered, verify_status FROM signals ORDER BY received_at DESC LIMIT ?", [limit])
    return JSONResponse({"ok": True, "count": len(rows), "signals": rows})


async def signals_count(request: Request) -> JSONResponse:
    db = get_db()
    total = await db.scalar("SELECT COUNT(*) AS c FROM signals")
    by_day = await db.execute("SELECT bar_date, COUNT(*) AS n, SUM(c_fired) AS c_n FROM signals GROUP BY bar_date ORDER BY bar_date DESC LIMIT 30")
    return JSONResponse({"ok": True, "total": total, "by_day": by_day})


async def export_csv(request: Request) -> Response:
    if not _secret_ok(request):
        return JSONResponse({"ok": False, "error": "invalid secret"}, status_code=403)
    limit = min(max(int(request.query_params.get("limit", "2000")), 1), 20000)
    rows = await get_db().execute(
        "SELECT s.*, t.d1_ret_pct, t.d3_ret_pct, t.d5_ret_pct, t.d10_ret_pct, t.d20_ret_pct, t.max_up_pct_20, t.max_dn_pct_20, "
        "t.hit_mid_day, t.hit_target_day, t.broke_stop_day, t.first_event, t.outcome, t.status AS track_status "
        "FROM signals s LEFT JOIN tracking t ON t.signal_id = s.signal_id ORDER BY s.bar_date DESC, s.symbol LIMIT ?", [limit])
    out = io.StringIO()
    if rows:
        cols = [c for c in rows[0].keys() if c not in ("payload_json", "metrics_json", "verify_json")]
        w = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return Response(content=out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=signals.csv"})


def _summary_route(fn):
    async def handler(request: Request) -> JSONResponse:
        if not _secret_ok(request):
            return JSONResponse({"ok": False, "error": "invalid secret"}, status_code=403)
        date = request.query_params.get("date") or request.query_params.get("month")
        force = request.query_params.get("force", "false").lower() in ("1", "true", "yes")
        try:
            return JSONResponse(await fn(get_db(), date, force))
        except Exception as exc:  # noqa: BLE001
            await discord.error("🚨 まとめ配信エラー %s: %s" % (fn.__name__, str(exc)[:300]))
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    return handler


async def receive_check(request: Request) -> JSONResponse:
    if not _secret_ok(request):
        return JSONResponse({"ok": False, "error": "invalid secret"}, status_code=403)
    expected = int(request.query_params.get("expected", "0"))
    return JSONResponse(await summaries.receive_check(get_db(), request.query_params.get("date"), expected))


async def stock_master_status(request: Request) -> JSONResponse:
    force = request.query_params.get("force", "false").lower() in ("1", "true")
    m = await market.load_stock_master(force=force)
    return JSONResponse({"ok": True, "count": len(m), "sample": dict(list(m.items())[:3])})


routes = [
    Route("/", health),
    Route("/webhook/tradingview", webhook_tradingview, methods=["POST"]),
    Route("/signals/recent", signals_recent),
    Route("/signals/count", signals_count),
    Route("/export/signals.csv", export_csv),
    Route("/summary/close-digest", _summary_route(summaries.close_digest)),
    Route("/summary/morning", _summary_route(summaries.morning)),
    Route("/summary/monthly", _summary_route(summaries.monthly)),
    Route("/ops/receive-check", receive_check),
    Route("/stock-master/status", stock_master_status),
]

# Starlette の新しい版では on_startup が無くなっているので lifespan を使う。
# （on_startup のままだと読み込みの時点で TypeError になり、Render は
#   「Exited with status 1」だけを残して落ちる。原因が見えないので注意）
@asynccontextmanager
async def lifespan(app_):
    await startup()
    yield


app = Starlette(routes=routes, lifespan=lifespan)
