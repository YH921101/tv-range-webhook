"""チャート画像（Pillow）。旧 Render の描画を土台に、描くものをチャネル・反応点・ゾーンに変えた。

色は日本式（陽線＝赤、陰線＝青）。土台の四分類でチャネルの色を変える。
日本語フォントは CHART_FONT_PATH にファイルを置いたときだけ使う（無ければ英数字のみ）。
"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import config
from .indicators import Bar, Channel, Touches, sma, touches

CLASS_COLOR = {
    "uptrend_pause": (29, 95, 104),
    "bottoming": (43, 108, 168),
    "directionless": (138, 148, 158),
    "downtrend": (201, 122, 30),
}
CLASS_LABEL = {"uptrend_pause": "uptrend pause", "bottoming": "bottoming", "directionless": "directionless", "downtrend": "downtrend"}
UP_COLOR = (193, 64, 60)
DN_COLOR = (43, 108, 168)


def _font(size: int):
    from PIL import ImageFont
    if config.CHART_FONT_PATH:
        try:
            return ImageFont.truetype(config.CHART_FONT_PATH, size)
        except Exception:  # noqa: BLE001
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render_png(row: Dict[str, Any], bars: Sequence[Bar], touch_info: Optional[Touches] = None) -> bytes:
    from PIL import Image, ImageDraw

    bars = list(bars)[-config.CHART_BARS:]
    if len(bars) < 20:
        raise ValueError("not enough bars")
    closes = [b.close for b in bars]
    width, height = 1200, 760
    left, right, top = 72, 116, 58
    price_bottom, volume_top, volume_bottom = 520, 560, 704
    chart_w = width - left - right
    price_h = price_bottom - top
    vol_h = volume_bottom - volume_top
    extend = 10

    length = int(row.get("ch_len") or 30)
    upper, lower, mid = row.get("ch_upper"), row.get("ch_lower"), row.get("ch_mid")
    slope = row.get("ch_slope") or 0.0
    off = (upper - lower) / 2.0 if upper and lower else 0.0
    cls = row.get("base_class") or "directionless"
    ccol = CLASS_COLOR.get(cls, CLASS_COLOR["directionless"])

    # チャネルの座標（最終足を i=0 として、i 本前の中心 = mid - slope*i）
    n = len(bars)
    ch_start = max(0, n - length)
    ys_mid = {}
    for idx in range(ch_start, n + extend):
        i = (n - 1) - idx
        ys_mid[idx] = mid - slope * i if mid is not None else None

    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    pv = highs + lows
    if upper and lower:
        pv += [upper + abs(slope) * extend, lower - abs(slope) * extend]
    min_p, max_p = min(pv), max(pv)
    pad = max((max_p - min_p) * 0.08, max_p * 0.01)
    min_p -= pad
    max_p += pad

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img, "RGBA")
    f_small = _font(12)
    f_title = _font(15)

    def x_at(idx: float) -> float:
        return left + chart_w * idx / max(n - 1 + extend, 1)

    def y_at(p: float) -> float:
        return price_bottom - (p - min_p) / (max_p - min_p) * price_h

    # 見出し
    title = "%s %s | %s | %s | pos %s%% | touch up %s / dn %s | grade %s" % (
        row.get("symbol") or "", _ascii(row.get("name") or ""), row.get("tf") or "1D", CLASS_LABEL.get(cls, cls),
        _fmt(row.get("pos_pct"), 0), row.get("touch_up"), row.get("touch_dn"), row.get("quality_grade") or "-")
    draw.text((left, 18), title, fill=(23, 33, 43, 255), font=f_title)
    draw.text((left, 38), "channel len %d  width %s%% / %s ATR  slope/ATR %s  eff %s  stab %s   MA5 MA20 MA50 MA100" % (
        length, _fmt(row.get("width_pct"), 1), _fmt(row.get("width_atr"), 1), _fmt(row.get("slope_atr"), 2),
        _fmt(row.get("eff_ratio"), 2), _fmt(row.get("stability"), 2)), fill=(90, 102, 114, 255), font=f_small)

    # 目盛り
    for k in range(6):
        r = k / 5
        y = top + price_h * r
        label = max_p - (max_p - min_p) * r
        draw.line((left, y, width - right, y), fill=(229, 231, 235, 255), width=1)
        draw.text((width - right + 8, y - 7), _fmt(label, 0), fill=(90, 102, 114, 255), font=f_small)

    # ゾーンの塗り（買い 0〜15％、売り 85〜100％）と枠
    if upper and lower and off > 0:
        pts_up, pts_lo, pts_mid = [], [], []
        zb = 2 * off * 0.15
        pts_bz, pts_sz = [], []
        for idx in range(ch_start, n + extend):
            m = ys_mid[idx]
            x = x_at(idx)
            pts_up.append((x, y_at(m + off)))
            pts_lo.append((x, y_at(m - off)))
            pts_mid.append((x, y_at(m)))
            pts_bz.append((x, y_at(m - off + zb)))
            pts_sz.append((x, y_at(m + off - zb)))
        draw.polygon(pts_lo + pts_bz[::-1], fill=DN_COLOR + (28,))
        draw.polygon(pts_sz + pts_up[::-1], fill=UP_COLOR + (28,))
        draw.line(pts_up, fill=ccol + (255,), width=2)
        draw.line(pts_lo, fill=ccol + (255,), width=2)
        _dashed_poly(draw, pts_mid, ccol + (170,), 1)
        # Raff の枠（影）
        a_up, a_lo = row.get("a_upper"), row.get("a_lower")
        if a_up and a_lo:
            a_off = (a_up - a_lo) / 2.0
            pa_up = [(x_at(idx), y_at(ys_mid[idx] + a_off)) for idx in range(ch_start, n + extend)]
            pa_lo = [(x_at(idx), y_at(ys_mid[idx] - a_off)) for idx in range(ch_start, n + extend)]
            _dashed_poly(draw, pa_up, ccol + (70,), 1, dash=3, gap=5)
            _dashed_poly(draw, pa_lo, ccol + (70,), 1, dash=3, gap=5)
        draw.text((x_at(n + extend - 1) - 60, y_at(upper + slope * (extend - 1)) - 16, ), "100%", fill=ccol + (255,), font=f_small)
        draw.text((x_at(n + extend - 1) - 60, y_at(lower + slope * (extend - 1)) + 4), "0%", fill=ccol + (255,), font=f_small)

    # 根拠の水準（端の近くにある節目だけ、薄い横線）
    for key in ("round_up", "round_down", "body_low_20", "body_low_40", "body_low_60", "body_high_20", "body_high_40", "body_high_60"):
        v = row.get(key)
        if v and min_p < v < max_p and upper and lower:
            near_edge = abs(v - lower) <= (row.get("price") or v) * 0.015 or abs(v - upper) <= (row.get("price") or v) * 0.015
            if near_edge:
                y = y_at(v)
                _dashed_line(draw, left, y, width - right, y, (120, 120, 120, 120), 1, dash=4, gap=6)

    # 現在値
    price = row.get("price")
    if price:
        y = y_at(price)
        _dashed_line(draw, left, y, width - right, y, (2, 132, 199, 255), 2)
        draw.text((width - right + 8, y - 8), "CUR " + _fmt(price, 0), fill=(2, 132, 199, 255), font=f_small)

    # ローソク足（日本式：陽線=赤、陰線=青）
    cw = max(3, min(9, int(chart_w / max(n + extend, 1) * 0.55)))
    for idx, b in enumerate(bars):
        x = x_at(idx)
        col = UP_COLOR + (255,) if b.close >= b.open else DN_COLOR + (255,)
        draw.line((x, y_at(b.low), x, y_at(b.high)), fill=col, width=1)
        yo, yc = y_at(b.open), y_at(b.close)
        t_, b_ = min(yo, yc), max(yo, yc)
        if b_ - t_ < 2:
            b_ = t_ + 2
        draw.rectangle((x - cw / 2, t_, x + cw / 2, b_), fill=col, outline=col)

    # 移動平均線
    for period, col in ((5, (37, 99, 235, 255)), (20, (245, 158, 11, 255)), (50, (124, 58, 237, 255)), (100, (15, 118, 110, 255))):
        vals = sma(closes, period)
        pts = [(x_at(i), y_at(v)) for i, v in enumerate(vals) if v is not None]
        if len(pts) >= 2:
            draw.line(pts, fill=col, width=2)

    # 反応点
    if touch_info is None and upper and lower and off > 0 and n >= length:
        ch = Channel(length=length, mid=mid, slope=slope, dev=0.0, max_abs=0.0, pct_off=0.0, off=off, upper=upper, lower=lower)
        touch_info = touches(bars, ch)
    if touch_info:
        for offv in touch_info.dn_offsets:
            idx = n - 1 - offv
            if 0 <= idx < n:
                x, y = x_at(idx), y_at(bars[idx].low) + 8
                draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=DN_COLOR + (230,), outline=(255, 255, 255, 255))
        for offv in touch_info.up_offsets:
            idx = n - 1 - offv
            if 0 <= idx < n:
                x, y = x_at(idx), y_at(bars[idx].high) - 8
                draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=UP_COLOR + (230,), outline=(255, 255, 255, 255))

    # シグナルの印（最終足）
    if row.get("c_fired"):
        x = x_at(n - 1)
        if (row.get("c_side") or "") == "LONG":
            y = y_at(bars[-1].low) + 22
            draw.polygon([(x, y - 10), (x - 8, y + 4), (x + 8, y + 4)], fill=DN_COLOR + (255,))
        else:
            y = y_at(bars[-1].high) - 22
            draw.polygon([(x, y + 10), (x - 8, y - 4), (x + 8, y - 4)], fill=UP_COLOR + (255,))

    # 出来高
    vols = [b.volume for b in bars]
    mv = max(vols) if vols else 0
    for k in range(4):
        y = volume_top + vol_h * k / 3
        draw.line((left, y, width - right, y), fill=(229, 231, 235, 255), width=1)
    if mv > 0:
        for idx, b in enumerate(bars):
            x = x_at(idx)
            h = b.volume / mv * vol_h
            col = UP_COLOR + (110,) if b.close >= b.open else DN_COLOR + (110,)
            draw.rectangle((x - cw / 2, volume_bottom - h, x + cw / 2, volume_bottom), fill=col)
    draw.text((left, volume_top - 18), "Volume", fill=(90, 102, 114, 255), font=f_small)
    step = max(1, n // 8)
    for idx in range(0, n, step):
        x = x_at(idx)
        draw.line((x, volume_bottom, x, volume_bottom + 4), fill=(156, 163, 175, 255), width=1)
        draw.text((x - 14, volume_bottom + 8), bars[idx].date[5:], fill=(90, 102, 114, 255), font=f_small)

    # 凡例
    lg = "circle = touch (blue lower / red upper)   dotted = Raff width   shade = buy/sell zone"
    draw.text((left, height - 34), lg, fill=(120, 130, 140, 255), font=f_small)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _dashed_line(draw, x1, y1, x2, y2, fill, width=1, dash=8, gap=5):
    import math
    total = math.hypot(x2 - x1, y2 - y1)
    if total == 0:
        return
    ux, uy = (x2 - x1) / total, (y2 - y1) / total
    pos = 0.0
    while pos < total:
        end = min(pos + dash, total)
        draw.line((x1 + ux * pos, y1 + uy * pos, x1 + ux * end, y1 + uy * end), fill=fill, width=width)
        pos = end + gap


def _dashed_poly(draw, pts, fill, width=1, dash=8, gap=5):
    for i in range(1, len(pts)):
        _dashed_line(draw, pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1], fill, width, dash, gap)


def _fmt(v: Any, digits: int) -> str:
    if v is None:
        return "-"
    try:
        return ("%." + str(digits) + "f") % float(v)
    except (TypeError, ValueError):
        return str(v)


def _ascii(text: str) -> str:
    """既定フォントは日本語を描けないので、フォント未設定なら英数字だけ残す。"""
    if config.CHART_FONT_PATH:
        return text
    return "".join(ch for ch in text if ord(ch) < 128).strip()
