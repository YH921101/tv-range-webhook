"""AI コメント（OpenAI Responses API を httpx で直接叩く）。

旧 Render の 4行固定・失敗時は定型文、という作りを踏襲。
指示文から「確率は 50:50 固定」を外し、実測の到達率（あれば）を渡す。
"""
from __future__ import annotations

import base64
from typing import Any, Dict, Optional, Tuple

import httpx

from . import config

# =====================================================
# AI の仕事は2つだけ。
#   ① 図を読む   ── 数字に出ていないことを言う（ローソクの並び、ヒゲ、安値の切り上がり、箱らしさ）
#   ② 気をつけること ── 数字と図を合わせて初めて言えることを言う
# そのうえで、渡した材料の外にある見立てがあれば一言添えてもらう。
#
# 渡すのは「事実」だけにする。こちらが下した判断（品質A/B、信頼度の強弱、土台の四分類、
# 押し目や深追いといった種類の名前、節目の強弱）は渡さない。
# 判断を渡すと AI はそれを言い換えるだけになり、独自の見解が出てこない。
# =====================================================
_BASE = (
    "あなたは日本株の日足チャートを読む担当です。対象はレンジ（横ばい）の下端買い・上端売りで、"
    "シグナルが出るかどうかは別の仕組みが枠の形だけで機械的に決めています。あなたの役目は判定ではなく、"
    "その銘柄を人が見るときに知っておきたいことを述べることです。"
    "渡すのは観測した事実の数字だけで、評価やラベルは付けていません。評価はあなたが自分で組み立ててください。"
)
_RULES = (
    "守ること。"
    "（1）渡された数字をそのまま言い換えただけの文は書かないでください。価格・幅・位置％・出来高の倍率は"
    "すでに通知の別の行に出ているので、繰り返すと読む人の時間を奪います。"
    "（2）数字にない値を推測したり、画像から指標の値を読み取ろうとしないでください。"
    "（3）買え・売れといった断定はしないでください。"
    "（4）材料から何も言えないときは、その行に「特筆なし」と書いてください。無理に埋めないでください。"
    "（5）節目の価格は過去の値から機械的に拾ったもので、必ず効くとは限りません。"
    "上端までの道のりが素直かどうかの所見にだけ使ってください。"
)
_CHART_NOTE = (
    "日足チャートの画像を渡します。画像には回帰チャネルの上下端と中心、買いゾーンの帯、"
    "反応点の丸、発火の印、移動平均（5赤・10青・20橙・50緑・100紫）、下段に出来高が描かれています。"
    "ローソク足は日本式の配色で、上げた足（陽線）が赤、下げた足（陰線）が青です。"
    "欧米式の緑＝上げ・赤＝下げと取り違えないでください。出来高の棒も同じ配色です。"
    "画像と数字が食い違ったときは数字を優先してください。"
)
INSTRUCTIONS_WITH_CHART = _BASE + _CHART_NOTE + _RULES + (
    "出力は日本語で3行。各行はそれぞれ「形：」「気になる点：」「見立て：」で始めてください。"
    "「形：」には画像を見て初めて分かることを書いてください。ローソクの並び方、上ヒゲ・下ヒゲの長さと向き、"
    "安値や高値が切り上がっているか切り下がっているか、枠が箱らしく見えるか斜めに崩れかけているか、"
    "端の近くで値動きが細くなっているか、移動平均が団子になっているか開いているか、などです。"
    "「気になる点：」には、数字と図を合わせて初めて言えることを書いてください。"
    "単独の数字の言い換えではなく、二つ以上の事実が重なったときに意味を持つことです。"
    "「見立て：」には、渡された材料の外にあるあなたの見解を書いてください。"
    "この形と数字の組み合わせから何が起きやすいと考えるか、何を見て確かめればよいか、といったことです。"
    "各行は1文で、50字程度に収めてください。"
)
INSTRUCTIONS = _BASE + _RULES + (
    "画像はありません。出力は日本語で2行。各行はそれぞれ「気になる点：」「見立て：」で始めてください。"
    "「気になる点：」には、単独の数字の言い換えではなく、二つ以上の事実が重なったときに意味を持つことを書いてください。"
    "「見立て：」には、渡された材料の外にあるあなたの見解を書いてください。"
    "各行は1文で、50字程度に収めてください。"
)


async def comment(row: Dict[str, Any], stats: Optional[Dict[str, Any]] = None,
                  chart_png: Optional[bytes] = None, profile: str = "realtime") -> str:
    """4〜5行のコメント。chart_png を渡すとチャートも見せる（即時のみを想定）。"""
    if not config.OPENAI_API_KEY:
        return fallback(row, stats)
    model, effort = _profile(profile)
    use_chart = bool(chart_png) and config.AI_WITH_CHART
    content: list = [{"type": "input_text", "text": build_prompt(row, stats)}]
    if use_chart:
        content.append({
            "type": "input_image",
            "image_url": "data:image/png;base64," + base64.b64encode(chart_png).decode(),
            "detail": config.AI_IMAGE_DETAIL,
        })
    body = {
        "model": model,
        "instructions": INSTRUCTIONS_WITH_CHART if use_chart else INSTRUCTIONS,
        "input": [{"role": "user", "content": content}],
        "max_output_tokens": config.AI_MAX_OUTPUT_TOKENS,
        "reasoning": {"effort": effort},
    }
    headers = {"Authorization": "Bearer " + config.OPENAI_API_KEY, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=90 if use_chart else 40) as client:
            r = await client.post("https://api.openai.com/v1/responses", json=body, headers=headers)
            r.raise_for_status()
            text = extract_text(r.json()).strip()
            if not text:
                return fallback(row, stats)
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            row["ai_model"] = _tag(model, effort, use_chart)
            return "\n".join(lines[:5])
    except Exception as exc:  # noqa: BLE001
        print("openai error (%s, chart=%s): %s" % (model, use_chart, exc))
        # 画像つきで落ちたら、文字だけでもう一度だけ試す
        if use_chart:
            return await comment(row, stats, None, profile)
        return fallback(row, stats)


def _profile(profile: str) -> Tuple[str, str]:
    if profile == "digest":
        return config.AI_MODEL_DIGEST, config.AI_EFFORT_DIGEST
    return config.AI_MODEL_REALTIME, config.AI_EFFORT_REALTIME


def _tag(model: str, effort: str, with_chart: bool) -> str:
    return "%s/%s%s%s" % (model, effort, "+chart" if with_chart else "", "+levels" if config.AI_WITH_LEVELS else "")


def extract_text(j: Dict[str, Any]) -> str:
    parts = []
    for item in j.get("output", []) or []:
        for c in item.get("content", []) or []:
            if c.get("type") in ("output_text", "text") and c.get("text"):
                parts.append(str(c["text"]))
    return "\n".join(parts)


def build_prompt(row: Dict[str, Any], stats: Optional[Dict[str, Any]]) -> str:
    """AI に渡す材料。観測した事実だけを並べる。

    こちらが下した判断は入れない。具体的には品質A/B、信頼度の強弱と点数、
    土台の四分類、押し目や深追いといった種類の名前、節目の強弱、見送り理由。
    それらを渡すと AI はそれを言い換えるだけになる。
    """
    lines = [
        "【銘柄】%s %s（%s、日足）" % (row.get("symbol"), row.get("name") or "", row.get("exchange") or "-"),
        "【価格】%s（前日比 %s%%）／その日の値幅 ATR %s（価格の %s%%）" % (
            _f(row.get("price"), 0), _f(row.get("change_pct"), 1), _f(row.get("atr"), 0), _f(row.get("atr_pct") or _atr_pct(row), 1)),
        "【出来高】前日比 %s倍／20日平均比 %s倍" % (_f(row.get("vol_ratio_1"), 1), _f(row.get("vol_ratio_20"), 1)),
        "【前日終値からの始値の離れ】%s%%" % _f(row.get("gap_pct"), 1),
        "【いま引いている枠】上端 %s／中心 %s／下端 %s（直近%d本の回帰、幅は価格の %s%%＝ATR の %s倍）" % (
            _f(row.get("ch_upper"), 0), _f(row.get("ch_mid"), 0), _f(row.get("ch_lower"), 0),
            int(row.get("ch_len") or 0), _f(row.get("width_pct"), 1), _f(row.get("width_atr"), 1)),
        "【枠の中での位置】下端を0%%、上端を100%%として %s%%（0を下回ると下端の外）" % _f(row.get("pos_pct"), 1),
        "【枠の端で止まった回数】上端 %s回／下端 %s回（%d本のあいだ）" % (
            row.get("touch_up"), row.get("touch_dn"), int(row.get("ch_len") or 0)),
        "【測った数字】枠の傾き÷ATR %s／効率比 %s（1に近いほど一直線）／枠の線の動かなさ %s（1が不動）" % (
            _f(row.get("slope_atr")), _f(row.get("eff_ratio")), _f(row.get("stability"))),
    ]
    ma = " / ".join("%s %s" % (lbl, _f(row.get(k), 0)) for k, lbl in
                    (("ma5", "5MA"), ("ma10", "10MA"), ("ma20", "20MA"), ("ma50", "50MA"), ("ma100", "100MA"))
                    if row.get(k))
    if ma:
        lines.append("【移動平均】" + ma)
    marks = []
    for k, lbl in (("round_up", "上のキリ番"), ("round_down", "下のキリ番")):
        if row.get(k):
            marks.append("%s %s" % (lbl, _f(row.get(k), 0)))
    for k, lbl in (("body_high_20", "20日実体高値"), ("body_high_40", "40日実体高値"), ("body_high_60", "60日実体高値"),
                   ("body_low_20", "20日実体安値"), ("body_low_40", "40日実体安値"), ("body_low_60", "60日実体安値")):
        if row.get(k):
            marks.append("%s %s" % (lbl, _f(row.get(k), 0)))
    if marks:
        lines.append("【価格の目印】" + " / ".join(marks))
    vz = []
    for k, lbl in (("vz1", "直近20日"), ("vz2", "21〜40日"), ("vz3", "41〜60日")):
        lo, hi, ratio = row.get(k + "_low"), row.get(k + "_high"), row.get(k + "_ratio")
        if lo and hi:
            vz.append("%sで最も出来高の多かった日の実体 %s〜%s（20日平均の %s倍）" % (lbl, _f(lo, 0), _f(hi, 0), _f(ratio, 1)))
    if vz:
        lines.append("【出来高の多かった価格帯】" + " / ".join(vz))
    if row.get("above_ma100") is not None:
        lines.append("【100日移動平均との関係】終値はその%s" % ("上" if row.get("above_ma100") else "下"))
    # 枠の中の節目。価格帯とそこに重なっている目印の名前だけ。強弱の点数はこちらの配点なので渡さない。
    if config.AI_WITH_LEVELS and row.get("levels"):
        from . import levels as _levels
        t = _levels.ai_text(row["levels"])
        if t:
            lines.append("【枠の中で目印が重なっている価格帯】" + t)
    if stats and stats.get("n"):
        lines.append("【同じような場面の過去の実績】%d件、反対側の端に到達 %s%%、平均 %s日、10日後平均 %s%%" % (
            stats["n"], _f(stats.get("reach_rate_pct"), 0), _f(stats.get("avg_days"), 1), _f(stats.get("avg_ret_10d"), 1)))
    return "\n".join(lines)


def fallback(row: Dict[str, Any], stats: Optional[Dict[str, Any]]) -> str:
    """OpenAI のキーが無いとき、または呼び出しが失敗したとき。

    数字の言い換えを機械で書いても、同じ通知の上の行と重複するだけで読む人の役に立たない。
    ここでは所見が無いことだけを伝える。
    """
    return "形：（AIの所見なし。OPENAI_API_KEY が未設定か、呼び出しに失敗）"


def _atr_pct(row: Dict[str, Any]) -> Optional[float]:
    try:
        atr, price = float(row.get("atr") or 0), float(row.get("price") or 0)
    except (TypeError, ValueError):
        return None
    return atr / price * 100.0 if atr and price else None


def _f(v: Any, digits: int = 2) -> str:
    if v is None:
        return "-"
    try:
        return ("%." + str(digits) + "f") % float(v)
    except (TypeError, ValueError):
        return str(v)
