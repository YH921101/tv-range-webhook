"""銘柄マスタ（社名・市場・業種）と地合い。"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from . import config
from .db import Database

_CACHE: Dict[str, Dict[str, str]] = {}
_LOADED_AT: Optional[datetime] = None


async def load_stock_master(force: bool = False) -> Dict[str, Dict[str, str]]:
    """銘柄マスタ（symbol,name,market,sector のヘッダ付き CSV）を読む。

    読む順番は2つ。
      1. リポジトリの中のファイル（既定 data_j.csv）。Render はリポジトリを丸ごと
         コピーして動くので、ファイルはもう手元にある。URL も合言葉も要らない。
         非公開リポジトリの raw アドレスは期限付きのトークンが付くため、
         URL で取りに行く形は途中で必ず失敗する。こちらを既定にする。
      2. STOCK_MASTER_CSV_URL（公開されている CSV を使いたいときだけ）
    どちらも無ければ空のまま。銘柄名は Pine から来る英語表記がそのまま出る。
    """
    global _CACHE, _LOADED_AT
    if not force and _LOADED_AT and (datetime.now() - _LOADED_AT).total_seconds() < config.STOCK_MASTER_CACHE_SECONDS:
        return _CACHE
    text = ""
    src = ""
    path = _resolve_master_path()
    if path:
        try:
            with open(path, "rb") as f:
                text = f.read().decode("utf-8-sig", errors="replace")
            src = "file:" + os.path.basename(path)
        except Exception as exc:  # noqa: BLE001
            print("stock master file read failed (%s): %s" % (path, exc))
    if not text and config.STOCK_MASTER_CSV_URL:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                r = await client.get(config.STOCK_MASTER_CSV_URL)
                r.raise_for_status()
                text = r.content.decode("utf-8-sig", errors="replace")
            src = "url"
        except Exception as exc:  # noqa: BLE001
            print("stock master url load failed: " + str(exc))
    if not text:
        return _CACHE
    try:
        reader = csv.DictReader(io.StringIO(text))
        out: Dict[str, Dict[str, str]] = {}
        for rec in reader:
            code = str(rec.get("symbol") or rec.get("code") or rec.get("コード") or "").strip().upper()
            if not code:
                continue
            out[code] = {
                "name": str(rec.get("name") or rec.get("銘柄名") or "").strip(),
                "market": str(rec.get("market") or rec.get("市場") or rec.get("市場・商品区分") or "").strip(),
                "sector": str(rec.get("sector") or rec.get("業種") or rec.get("33業種区分") or "").strip(),
            }
        _CACHE = out
        _LOADED_AT = datetime.now()
        print("stock master loaded: %d銘柄 (%s)" % (len(out), src))
    except Exception as exc:  # noqa: BLE001
        print("stock master parse failed: " + str(exc))
    return _CACHE


def _resolve_master_path() -> Optional[str]:
    """リポジトリの中の銘柄マスタを探す。app/ の1つ上（リポジトリの一番上）を見る。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    names = [config.STOCK_MASTER_CSV_PATH] if config.STOCK_MASTER_CSV_PATH else []
    names += ["data_j.csv", "stock_master.csv"]
    for n in names:
        if not n:
            continue
        cand = n if os.path.isabs(n) else os.path.join(root, n)
        if os.path.exists(cand):
            return cand
    return None


async def enrich(row: Dict[str, Any], db: Database) -> None:
    master = await load_stock_master()
    e = master.get(row.get("symbol") or "")
    if not e:
        r = await db.fetch_one("SELECT name, market, sector FROM stock_master WHERE symbol = ?", [row.get("symbol")])
        e = dict(r) if r else None
    if e:
        # 銘柄名は銘柄マスタを優先する。Pine から来る名前は取引所の英語表記
        # （RAKUTEN BANK）なので、日本語名があればそちらに差し替える。
        if e.get("name"):
            row["name"] = e["name"]
        row["market"] = e.get("market") or row.get("market")
        row["sector"] = e.get("sector") or row.get("sector")


def _fl(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
