"""Yahoo Finance の日足取得（旧 Render から移植）。非公式の入口なので、取れなければ None を返す。"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import httpx

from .indicators import Bar
from . import config

JST = timezone(timedelta(hours=9))


def yahoo_quote(symbol: str, exchange: str = "", tickerid: str = "") -> str:
    sym = symbol.strip().upper()
    if ":" in tickerid:
        sym = tickerid.split(":")[-1].strip().upper()
    if not sym:
        return ""
    ex = (exchange or (tickerid.split(":")[0] if ":" in tickerid else "")).upper()
    if sym.endswith(".T"):
        return sym
    if ex in ("TSE", "TYO", "JPX", "") and sym[:1].isdigit():
        return sym + ".T"
    return sym


async def fetch_daily_bars(symbol: str, exchange: str = "", tickerid: str = "", rng: str = "") -> Optional[List[Bar]]:
    quote = yahoo_quote(symbol, exchange, tickerid)
    if not quote:
        return None
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + quote
    params = {"range": rng or config.YAHOO_RANGE, "interval": "1d", "includePrePost": "false", "events": "history"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return parse_chart(r.json())
    except Exception as exc:  # noqa: BLE001
        print("yahoo fetch failed: " + quote + " " + str(exc))
        return None


def parse_chart(data: Dict[str, Any]) -> List[Bar]:
    result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
    if not result:
        return []
    ts = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs, lows, closes, vols = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("close") or [], q.get("volume") or []
    bars: List[Bar] = []
    for i, t in enumerate(ts):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        except IndexError:
            continue
        if None in (o, h, l, c):
            continue
        v = vols[i] if i < len(vols) and vols[i] is not None else 0
        d = datetime.fromtimestamp(int(t), JST).strftime("%Y-%m-%d")
        bars.append(Bar(date=d, open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v)))
    return bars


def bars_up_to(bars: List[Bar], bar_date: str) -> List[Bar]:
    """bar_date までの足に切り詰める（発生日より後の足を再検証に混ぜない）。"""
    return [b for b in bars if b.date <= bar_date]
