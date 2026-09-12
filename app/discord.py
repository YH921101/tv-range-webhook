"""Discord への送信（旧 Render から移植）。再試行・送信間隔・フォーラム投稿・画像添付・1,800字分割。"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx

from . import config

_LOCK = asyncio.Lock()


async def _request_with_retry(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> Optional[httpx.Response]:
    last: Optional[httpx.Response] = None
    for attempt in range(config.DISCORD_MAX_RETRIES):
        try:
            resp = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            print("discord request error: " + str(exc))
            await asyncio.sleep(config.DISCORD_BASE_DELAY_SECONDS * (2 ** attempt))
            continue
        last = resp
        if resp.status_code == 429:
            await asyncio.sleep(_retry_after(resp))
            continue
        if resp.status_code >= 500:
            await asyncio.sleep(config.DISCORD_BASE_DELAY_SECONDS * (2 ** attempt))
            continue
        return resp
    return last


def _retry_after(resp: httpx.Response) -> float:
    try:
        return float(resp.json().get("retry_after", 1.0)) + 0.2
    except Exception:  # noqa: BLE001
        pass
    try:
        return float(resp.headers.get("Retry-After", "1")) + 0.2
    except ValueError:
        return 1.2


def split_message(message: str, max_chars: int = 1800) -> List[str]:
    if len(message) <= max_chars:
        return [message]
    parts: List[str] = []
    buf = ""
    for line in message.split("\n"):
        if len(buf) + len(line) + 1 > max_chars:
            parts.append(buf)
            buf = line
        else:
            buf = line if not buf else buf + "\n" + line
    if buf:
        parts.append(buf)
    return parts


def truncate_thread_name(name: str, max_len: int = 100) -> str:
    return name if len(name) <= max_len else name[: max_len - 1] + "…"


async def post(webhook_url: str, message: str, thread_name: Optional[str] = None,
               attachment: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """通常チャンネルにもフォーラムにも使える。フォーラムなら thread_name を渡す。
    戻り値：最初の投稿の URL。取れなければ None。"""
    if not webhook_url:
        return None
    chunks = split_message(message)
    url_out: Optional[str] = None
    thread_id: Optional[str] = None
    async with _LOCK:
        async with httpx.AsyncClient(timeout=30) as client:
            for i, chunk in enumerate(chunks):
                payload: Dict[str, Any] = {"content": chunk, "allowed_mentions": {"parse": []}}
                params: Dict[str, Any] = {"wait": "true"}
                if thread_name:
                    if i == 0:
                        payload["thread_name"] = truncate_thread_name(thread_name)
                    elif thread_id:
                        params["thread_id"] = thread_id
                if i == 0 and attachment:
                    files = {"files[0]": (attachment["filename"], attachment["data"], attachment.get("content_type", "image/png"))}
                    data = {"payload_json": json.dumps(payload, ensure_ascii=False)}
                    resp = await _request_with_retry(client, "POST", webhook_url, params=params, data=data, files=files)
                else:
                    resp = await _request_with_retry(client, "POST", webhook_url, params=params, json=payload)
                if resp is None or resp.status_code >= 400:
                    detail = "no response" if resp is None else "%d %s" % (resp.status_code, resp.text[:200])
                    print("discord post failed: " + detail)
                    break
                if i == 0:
                    try:
                        j = resp.json()
                        url_out = _message_url(j)
                        if thread_name:
                            thread_id = str(j.get("channel_id") or "") or None
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(config.DISCORD_SEND_SPACING_SECONDS)
    return url_out


def thread_id_from_url(url: Optional[str]) -> Optional[str]:
    """スレッドの URL からスレッド ID を取り出す（/channels/<guild>/<thread>/<message>）。"""
    if not url:
        return None
    parts = [p for p in str(url).split("/") if p]
    return parts[-2] if len(parts) >= 2 and parts[-2].isdigit() else None


async def post_in_thread(webhook_url: str, thread_id: str, message: str) -> Optional[str]:
    """既にあるスレッドに追記する。銘柄ごとの記録を1本にまとめるため。"""
    if not webhook_url or not thread_id:
        return None
    async with _LOCK:
        async with httpx.AsyncClient(timeout=30) as client:
            for chunk in split_message(message):
                resp = await _request_with_retry(
                    client, "POST", webhook_url, params={"wait": "true", "thread_id": thread_id},
                    json={"content": chunk, "allowed_mentions": {"parse": []}})
                if resp is None or resp.status_code >= 400:
                    print("discord thread post failed")
                    return None
                await asyncio.sleep(config.DISCORD_SEND_SPACING_SECONDS)
    return "ok"


def _message_url(j: Dict[str, Any]) -> Optional[str]:
    guild = j.get("guild_id")
    channel = j.get("channel_id")
    mid = j.get("id")
    if channel and mid:
        return "https://discord.com/channels/%s/%s/%s" % (guild or "@me", channel, mid)
    return None


async def error(message: str) -> None:
    """エラーの知らせ。ここが黙ると異常に気づけないので、通常チャンネルとして
    送って通らなかったときはフォーラムの形でもう一度だけ試す。"""
    url = config.DISCORD_WEBHOOK_URL_ERROR
    if not url:
        print("error channel not configured: " + message[:200])
        return
    try:
        if await post(url, message):
            return
        print("error channel: 通常チャンネルとして送れず。フォーラムとして再試行")
        await post(url, message, thread_name="エラー " + _now_hm())
    except Exception as exc:  # noqa: BLE001
        print("error channel post failed: " + str(exc))


def _now_hm() -> str:
    from datetime import datetime
    return datetime.now(config.JST).strftime("%m-%d %H:%M")
