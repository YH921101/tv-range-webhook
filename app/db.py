"""保存先。Turso（libSQL の HTTP API）とローカル SQLite の二つを同じ形で扱う。

Turso は追加ライブラリなしで HTTP API（/v2/pipeline）を直接叩く。
ローカル開発では TURSO_DATABASE_URL を空にすれば SQLite ファイルに落ちる。
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx

from . import config

Row = Dict[str, Any]
Stmt = Tuple[str, Sequence[Any]]


class Database:
    def __init__(self, url: str = "", token: str = "", sqlite_path: str = "signals.db"):
        self.kind = "turso" if url.startswith(("libsql://", "https://", "wss://")) else "sqlite"
        self.sqlite_path = sqlite_path
        self.token = token
        if self.kind == "turso":
            host = url.split("://", 1)[1].rstrip("/")
            self.http_url = "https://" + host + "/v2/pipeline"
        else:
            self.http_url = ""
        self._client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()

    # ---------------- 公開 API ----------------
    async def execute(self, sql: str, args: Sequence[Any] = ()) -> List[Row]:
        rows, _ = await self._run([(sql, list(args))])
        return rows[0]

    async def execute_many(self, stmts: Sequence[Stmt]) -> List[List[Row]]:
        rows, _ = await self._run([(s, list(a)) for s, a in stmts])
        return rows

    async def fetch_one(self, sql: str, args: Sequence[Any] = ()) -> Optional[Row]:
        rows = await self.execute(sql, args)
        return rows[0] if rows else None

    async def scalar(self, sql: str, args: Sequence[Any] = ()) -> Any:
        row = await self.fetch_one(sql, args)
        if not row:
            return None
        return next(iter(row.values()))

    async def init_schema(self, schema_sql: str) -> None:
        stmts = [s.strip() for s in _split_sql(schema_sql) if s.strip()]
        for s in stmts:
            if s.upper().startswith("PRAGMA") and self.kind == "turso":
                continue
            await self.execute(s)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ---------------- 内部 ----------------
    async def _run(self, stmts: List[Stmt]) -> Tuple[List[List[Row]], List[int]]:
        if self.kind == "sqlite":
            return await asyncio.to_thread(self._run_sqlite, stmts)
        return await self._run_turso(stmts)

    def _run_sqlite(self, stmts: List[Stmt]) -> Tuple[List[List[Row]], List[int]]:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        out: List[List[Row]] = []
        affected: List[int] = []
        try:
            cur = conn.cursor()
            for sql, args in stmts:
                cur.execute(sql, tuple(args))
                if cur.description:
                    out.append([dict(r) for r in cur.fetchall()])
                else:
                    out.append([])
                affected.append(cur.rowcount if cur.rowcount is not None else 0)
            conn.commit()
        finally:
            conn.close()
        return out, affected

    async def _run_turso(self, stmts: List[Stmt]) -> Tuple[List[List[Row]], List[int]]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20)
        requests = [{"type": "execute", "stmt": {"sql": sql, "args": [_encode_arg(a) for a in args]}} for sql, args in stmts]
        requests.append({"type": "close"})
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        async with self._lock:
            resp = await self._client.post(self.http_url, json={"requests": requests}, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        out: List[List[Row]] = []
        affected: List[int] = []
        for item in data.get("results", [])[: len(stmts)]:
            if item.get("type") != "ok":
                err = item.get("error", {})
                raise RuntimeError("turso error: " + str(err.get("message", err)))
            result = item.get("response", {}).get("result", {})
            cols = [c.get("name") for c in result.get("cols", [])]
            rows = []
            for r in result.get("rows", []):
                rows.append({cols[i]: _decode_value(v) for i, v in enumerate(r)})
            out.append(rows)
            affected.append(int(result.get("affected_row_count", 0) or 0))
        return out, affected


def _encode_arg(a: Any) -> Dict[str, Any]:
    if a is None:
        return {"type": "null"}
    if isinstance(a, bool):
        return {"type": "integer", "value": "1" if a else "0"}
    if isinstance(a, int):
        return {"type": "integer", "value": str(a)}
    if isinstance(a, float):
        if a != a:  # NaN
            return {"type": "null"}
        return {"type": "float", "value": a}
    if isinstance(a, (bytes, bytearray)):
        import base64
        return {"type": "blob", "base64": base64.b64encode(bytes(a)).decode()}
    return {"type": "text", "value": str(a)}


def _decode_value(v: Dict[str, Any]) -> Any:
    t = v.get("type")
    if t == "null":
        return None
    if t == "integer":
        try:
            return int(v.get("value"))
        except (TypeError, ValueError):
            return v.get("value")
    if t == "float":
        return float(v.get("value"))
    if t == "blob":
        import base64
        return base64.b64decode(v.get("base64", ""))
    return v.get("value")


def _split_sql(sql: str) -> Iterable[str]:
    """';' で分割する。コメント行は落とす。文字列中の ';' は使っていない前提。"""
    buf: List[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
    text = "\n".join(buf)
    for part in text.split(";"):
        yield part


_db: Optional[Database] = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database(config.TURSO_DATABASE_URL, config.TURSO_AUTH_TOKEN, config.SQLITE_PATH)
    return _db


def schema_sql() -> str:
    path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
