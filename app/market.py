"""銘柄マスタ（社名・市場・業種）と地合い。"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from . import config
from .db import Database

_CACHE: Dict[str, Dict[str, str]] = {}
_LOADED_AT: Optional[datetime] = None


async def load_stock_master(force: bool = False) -> Dict[str, Dict[str, str]]:
    """CSV（symbol,name,market,sector のヘッダ付き）を URL から読む。無ければ空。"""
    global _CACHE, _LOADED_AT
    if not config.STOCK_MASTER_CSV_URL:
        return _CACHE
    if not force and _LOADED_AT and (datetime.now() - _LOADED_AT).total_seconds() < config.STOCK_MASTER_CACHE_SECONDS:
        return _CACHE
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(config.STOCK_MASTER_CSV_URL)
            r.raise_for_status()
            text = r.content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        out: Dict[str, Dict[str, str]] = {}
        for rec in reader:
            code = str(rec.get("symbol") or rec.get("code") or rec.get("コード") or "").strip().upper()
            if not code:
                continue
            out[code] = {
                "name": str(rec.get("name") or rec.get("銘柄名") or "").strip(),
                "market": str(rec.get("market") or rec.get("市場") or "").strip(),
                "sector": str(rec.get("sector") or rec.get("業種") or "").strip(),
            }
        _CACHE = out
        _LOADED_AT = datetime.now()
    except Exception as exc:  # noqa: BLE001
        print("stock master load failed: " + str(exc))
    return _CACHE


async def enrich(row: Dict[str, Any], db: Database) -> None:
    master = await load_stock_master()
    e = master.get(row.get("symbol") or "")
    if not e:
        r = await db.fetch_one("SELECT name, market, sector FROM stock_master WHERE symbol = ?", [row.get("symbol")])
        e = dict(r) if r else None
    if e:
        if e.get("name") and not row.get("name"):
            row["name"] = e["name"]
        row["market"] = e.get("market") or row.get("market")
        row["sector"] = e.get("sector") or row.get("sector")


def _fl(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
