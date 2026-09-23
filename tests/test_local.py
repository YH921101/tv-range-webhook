"""ローカルで一通り動かす確認。Discord / OpenAI / Yahoo には触らない。

実行: python -m tests.test_local
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import sys
from datetime import date, timedelta

os.environ["TURSO_DATABASE_URL"] = ""
os.environ["SQLITE_PATH"] = "/tmp/test_signals.db"
os.environ["DISCORD_WEBHOOK_URL_REALTIME"] = ""
os.environ["DISCORD_WEBHOOK_URL_DIGEST"] = ""
os.environ["DISCORD_WEBHOOK_URL_ERROR"] = ""
os.environ["OPENAI_API_KEY"] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import chart, db as dbmod, indicators, payload as pl, pipeline, summaries, yahoo  # noqa: E402
from app.indicators import Bar  # noqa: E402
from jobs.track import compute_tracking  # noqa: E402


def make_bars(n: int = 220, seed: int = 7) -> list:
    """上昇のあと、横ばい（箱）に入る日足を作る。"""
    random.seed(seed)
    bars = []
    d = date(2026, 1, 5)
    price = 2500.0
    for i in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        if i < 120:
            drift = 3.0
            noise = random.gauss(0, 25)
        else:
            # 3,380〜3,690 の箱。周期的に上下端に触れる
            center = 3535.0
            amp = 150.0
            drift = 0.0
            target = center + amp * math.sin((i - 120) / 6.0)
            noise = target - price + random.gauss(0, 12)
        o = price
        c = price + drift + noise
        h = max(o, c) + abs(random.gauss(0, 10))
        l = min(o, c) - abs(random.gauss(0, 10))
        v = 100000 + random.randint(0, 80000)
        bars.append(Bar(d.strftime("%Y-%m-%d"), round(o, 1), round(h, 1), round(l, 1), round(c, 1), v))
        price = c
        d += timedelta(days=1)
    return bars


def build_payload(bars: list, length: int = 30) -> dict:
    rc = indicators.recompute(bars, length, "stdev2")
    assert rc is not None
    ch = rc.channel
    last = bars[-1]
    pos = ch.pos_pct
    fired = pos is not None and pos <= 15.0
    side = "LONG" if fired else ""
    return {
        "v": 3, "pine_version": "range_v7.14", "mode": "signal",
        "signal_id": "7203_%s_%s" % (last.date.replace("-", ""), side or "SNAP"),
        "symbol": "7203", "name": "TOYOTA MOTOR", "exchange": "TSE", "tickerid": "TSE:7203",
        "tv_url": "https://www.tradingview.com/chart/?symbol=TSE:7203", "tf": "1D", "bar_date": last.date,
        "price": last.close, "open": last.open, "high": last.high, "low": last.low, "prev_close": bars[-2].close,
        "change_pct": (last.close / bars[-2].close - 1) * 100, "volume": last.volume, "vol_ratio_1": 1.1, "vol_ratio_20": 0.95, "atr": rc.atr,
        "channel": {"len": length, "width_type": "stdev2", "upper": ch.upper, "lower": ch.lower, "mid": ch.mid, "slope": ch.slope, "off": ch.off,
                    "width_pct": ch.width_pct, "width_atr": ch.width_atr, "pos_pct": pos},
        "features": {"slope_atr": rc.slope_atr, "eff_ratio": rc.eff_ratio, "touch_up": rc.touches.up, "touch_dn": rc.touches.dn,
                     "stability": rc.stability, "bull_bar": last.close > last.open, "upper_half": True, "gap_pct": 0.3, "event_bar": False, "vol_spike": False},
        "base": {"class": rc.base_class, "above_ma100": rc.above_ma100, "ma5": rc.ma["ma5"], "ma10": rc.ma["ma10"], "ma20": rc.ma["ma20"], "ma50": rc.ma["ma50"], "ma100": rc.ma["ma100"],
                 "ma20_chg5_pct": rc.ma20_chg5_pct, "ma100_chg20_pct": rc.ma100_chg20_pct, "w_ma5": None, "w_ma10": None, "w_ma20": None,
                 "w_ma5_chg1_pct": rc.w_ma5_chg1_pct, "w_ma10_chg1_pct": rc.w_ma10_chg1_pct},
        "gates": {"l1_flat": rc.l1_flat, "l1_base": True, "l2_width": rc.l2_width, "l2_stab": rc.l2_stab, "l2_touch_buy": rc.touches.dn >= 1,
                  "l2_touch_sell": rc.touches.up >= 1, "confirm_buy": True, "confirm_sell": True, "can_fade": True, "fail_reason": "-"},
        "signal": {"method": "C", "fired": fired, "side": side, "kind": "entry" if fired else "", "break_up": False, "break_down": False},
        "shadow": [{"method": "A", "width_type": "raff", "upper": rc.channel_a.upper, "lower": rc.channel_a.lower, "pos_pct": rc.channel_a.pos_pct, "fired": False, "side": ""},
                   {"method": "B", "upper": ch.upper, "lower": ch.lower, "pos_pct": pos, "fired": fired, "side": side}],
        # Pine が送ってくる入力値。Render はこれで再計算する
        "params": {"sigma_mult": 2.0, "pctile": 90.0, "max_slope_atr": 0.10, "max_eff_ratio": 0.35,
                   "flat_thr": -0.5, "touch_zone": 15.0, "touch_exit": 35.0, "min_width_pct": 0.0,
                   "min_width_atr": 3.0, "max_width_atr": 15.0, "stab_delta": 5, "min_stability": 0.7},
        "evidence": {"round_up": math.ceil(last.close / 100) * 100, "round_down": math.floor(last.close / 100) * 100,
                     "body_high_20": max(max(b.open, b.close) for b in bars[-21:-1]), "body_high_40": max(max(b.open, b.close) for b in bars[-41:-1]),
                     "body_high_60": max(max(b.open, b.close) for b in bars[-61:-1]),
                     "body_low_20": min(min(b.open, b.close) for b in bars[-21:-1]), "body_low_40": min(min(b.open, b.close) for b in bars[-41:-1]),
                     "body_low_60": min(min(b.open, b.close) for b in bars[-61:-1]),
                     "volzone_1_20": {"low": ch.lower - 5, "high": ch.lower + 25, "ratio": 1.6},
                     "volzone_21_40": {"low": ch.mid, "high": ch.mid + 40, "ratio": 1.1},
                     "volzone_41_60": {"low": ch.upper - 30, "high": ch.upper + 10, "ratio": 2.1}},
    }


async def main() -> None:
    if os.path.exists("/tmp/test_signals.db"):
        os.remove("/tmp/test_signals.db")
    db = dbmod.get_db()
    await db.init_schema(dbmod.schema_sql())

    bars = make_bars()
    # 位置％が 15％以下になる足を探して、そこを発生日にする
    idx = None
    for k in range(160, len(bars)):
        rc = indicators.recompute(bars[: k + 1], 30, "stdev2")
        if rc and rc.channel.pos_pct is not None and rc.channel.pos_pct <= 15 and rc.touches.dn >= 1:
            idx = k
            break
    assert idx is not None, "no fired bar found"
    fired_bars = bars[: idx + 1]
    payload = build_payload(fired_bars)
    print("payload class=%s pos=%.1f touches=%s/%s stab=%s fired=%s" % (
        payload["base"]["class"], payload["channel"]["pos_pct"], payload["features"]["touch_up"], payload["features"]["touch_dn"],
        payload["features"]["stability"], payload["signal"]["fired"]))

    # Yahoo を差し替え
    async def fake_fetch(symbol, exchange="", tickerid="", rng=""):
        return bars
    yahoo.fetch_daily_bars = fake_fetch  # type: ignore[assignment]
    pipeline.yahoo.fetch_daily_bars = fake_fetch  # type: ignore[attr-defined]

    errs, warns = pl.validate(payload)
    assert not errs, errs
    res = await pipeline.process_payload(db, payload, "2026-09-09T15:31:00+09:00")
    print("pipeline:", res)
    row = await db.fetch_one("SELECT * FROM signals WHERE signal_id = ?", [payload["signal_id"]])
    assert row is not None
    print("stored: grade=%s verify=%s delivered=%s upper_reason=%s lower_reason=%s" % (
        row["quality_grade"], row["verify_status"], row["delivered"], row["upper_reason"], row["lower_reason"]))
    assert row["verify_status"] in ("match", "mismatch"), row["verify_status"]
    if row["verify_status"] == "mismatch":
        print("  diffs:", json.loads(row["verify_json"]).get("diffs"))

    # 信頼度は加点だけ。点数と内訳が入っていること
    print("信頼度: %s %s点｜%s" % (row["confidence"], row["confidence_score"], row["confidence_detail"]))
    assert row["confidence"] in ("強", "中", "弱"), row["confidence"]
    assert (row["confidence_score"] or 0) > 0, row["confidence_score"]
    assert row["confidence_detail"], "内訳が空"
    # 加点だけなので、端の支持だけのときより必ず大きいか等しい
    assert (row["confidence_score"] or 0) >= min(json.loads(row["metrics_json"] or "{}").get("lower_score", 0) or 0, 10.0) - 0.001

    # 枠の中の節目。出ないこともある（枠が狭いと端の根拠に吸収される）ので、形だけ見る
    zs = json.loads(row["levels_json"]) if row["levels_json"] else []
    print("節目 %d本" % len(zs))
    for z in zs:
        assert z["side"] in ("up", "down") and z["low"] <= z["high"]
        assert z["zone"] in ("in_up", "in_down", "out_up", "out_down"), z
        # 区分どおりの位置にあること
        if z["zone"] == "out_up":
            assert z["low"] >= row["ch_upper"] - 1, z
        elif z["zone"] == "out_down":
            assert z["high"] <= row["ch_lower"] + 1, z
        else:
            assert row["ch_lower"] - 1 <= z["low"] and z["high"] <= row["ch_upper"] + 1, z
        print("   %s %d〜%d（%s）[%s] %+.1f%%" % (
            z["zone"], z["low"], z["high"], z["label_text"], z["strength"],
            z["distance_pct"] if z["side"] == "up" else -z["distance_pct"]))
    tr = await db.fetch_one("SELECT * FROM tracking WHERE signal_id = ?", [payload["signal_id"]])
    assert tr is not None and tr["status"] == "open"

    # 同じものを二度送っても一行
    res2 = await pipeline.process_payload(db, payload, "2026-09-09T15:31:05+09:00")
    assert res2.get("duplicate"), res2
    n = await db.scalar("SELECT COUNT(*) AS c FROM signals")
    assert n == 1, n

    # 画像
    png = chart.render_png(dict(row), fired_bars)
    with open("/tmp/test_chart.png", "wb") as f:
        f.write(png)
    print("chart bytes:", len(png))

    # JSON の構造がずれたら配信しない（本当の互換性チェック）
    bad = dict(payload)
    bad["v"] = 2
    bad["signal_id"] = "X_BAD"
    r3 = await pipeline.process_payload(db, bad, "2026-09-09T15:32:00+09:00")
    assert r3.get("ok") is False and r3.get("errors")

    # Pine の版ずれは止めない（記録だけ。EXPECTED_PINE_VERSION が空なので照合もしない）
    old_pine = dict(payload)
    old_pine["pine_version"] = "range_v6"
    old_pine["signal_id"] = "X_OLDPINE"
    r4 = await pipeline.process_payload(db, old_pine, "2026-09-09T15:33:00+09:00")
    # 検証を通り抜けていること（ここで止まるのは同じ銘柄の再通知抑制だけ）
    assert r4.get("ok") is True and not r4.get("errors"), r4
    print("old pine version: passed validation, reason =", r4.get("reason"))

    # まとめ配信（Discord 無しなので url は None）
    dg = await summaries.close_digest(db, fired_bars[-1].date, True)
    print("digest:", dg)
    assert dg["count"] >= 1

    # 追跡の計算
    post = bars[idx + 1: idx + 31]
    tres = compute_tracking("LONG", float(payload["price"]), payload["channel"]["mid"], payload["channel"]["upper"], payload["channel"]["lower"], post)
    print("tracking:", {k: tres[k] for k in ("d5_ret_pct", "max_up_pct_20", "max_dn_pct_20", "hit_mid_day", "hit_target_day", "broke_stop_day", "first_event", "outcome", "status")})
    assert tres["status"] == "done"

    # Turso の引数の変換
    enc = [dbmod._encode_arg(x) for x in (None, 1, 2.5, "a", True)]
    assert enc[0]["type"] == "null" and enc[1] == {"type": "integer", "value": "1"} and enc[2]["type"] == "float" and enc[4]["value"] == "1"
    assert dbmod._decode_value({"type": "integer", "value": "12"}) == 12

    # ウォッチリスト一括アラート（軽い JSON）を膨らませて通す
    chn = payload["channel"]; fea = payload["features"]
    thin = {
        "v": 3, "src": "wl", "pine_version": "range_v7.14", "mode": "signal", "side": "LONG",
        "symbol": "6758", "tf": "1D", "bar_time": fired_bars[-1].date + "T06:00:00Z",
        "price": payload["price"], "open": payload["open"], "high": payload["high"], "low": payload["low"],
        "volume": payload["volume"],
        "wl": {"kind": 1, "class": 1, "len": chn["len"], "upper": chn["upper"], "lower": chn["lower"],
               "mid": chn["mid"], "atr": payload["atr"], "pos": chn["pos_pct"],
               "slope_x100": round((fea["slope_atr"] or 0) * 100, 1), "eff_x100": round((fea["eff_ratio"] or 0) * 100, 1),
               "tup": fea["touch_up"], "tdn": fea["touch_dn"], "stab_x100": round((fea["stability"] or 0) * 100, 1),
               "wpct_x10": round(chn["width_pct"] * 10, 1), "watr_x10": round(chn["width_atr"] * 10, 1)},
    }
    expanded = pl.expand_watchlist(thin)
    e_errs, _ = pl.validate(expanded)
    assert not e_errs, e_errs
    assert expanded["bar_date"] == fired_bars[-1].date, expanded["bar_date"]
    assert abs(expanded["features"]["stability"] - (fea["stability"] or 0)) < 0.01
    assert expanded["signal"]["fired"] and expanded["signal"]["side"] == "LONG"
    rwl = await pipeline.process_payload(db, thin, "2026-09-09T15:33:00+09:00")
    print("watchlist:", rwl)
    assert rwl.get("ok"), rwl
    wrow = await db.fetch_one("SELECT * FROM signals WHERE symbol = ?", ["6758"])
    assert wrow is not None and wrow["c_fired"] == 1, wrow
    print("watchlist stored: grade=%s pos=%.1f touches=%s/%s" % (wrow["quality_grade"], wrow["pos_pct"], wrow["touch_up"], wrow["touch_dn"]))

    # 受信ログと件数照合
    rc_ = await summaries.receive_check(db, "2026-09-09", 0)
    # main.py そのものが読み込めて、起動処理が通ること。
    # ここを試していなかったので、Starlette の版が上がって on_startup が
    # 無くなったのに気づかず、Render で「Exited with status 1」になった。
    import importlib
    import starlette
    main_mod = importlib.import_module("main")
    print("starlette %s / main.py 読み込み ok / ルート %d本" % (starlette.__version__, len(main_mod.routes)))
    await main_mod.startup()
    print("startup:", main_mod.STARTUP)
    assert main_mod.STARTUP.get("schema") == "ok", main_mod.STARTUP
    res_health = await main_mod.health(None)  # type: ignore[arg-type]
    assert res_health.status_code == 200

    print("receive check:", rc_)
    await db.close()
    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(main())
