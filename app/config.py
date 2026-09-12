"""環境変数の読み込み。Render のダッシュボードで設定する。"""
import os
from datetime import timezone, timedelta

JST = timezone(timedelta(hours=9))


def _str(name: str, default: str = "") -> str:
    """空欄は「未設定」として扱う。

    Render のダッシュボードでキーだけ作って値を入れ忘れると、os.getenv は
    None ではなく空文字を返す。そのままだと既定値に戻らないので、ここで吸収する。
    前後の空白も落とす（モデル名の末尾に空白が入るとそのままエラーになる）。
    """
    v = os.getenv(name)
    return default if v is None or not v.strip() else v.strip()


def _bool(name: str, default: bool) -> bool:
    v = _str(name)
    if not v:
        return default
    return v.lower() not in ("0", "false", "no", "off")


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except ValueError:
        return default


APP_VERSION = "range-v1.0"

# ---- Pine との握手 ----
# JSON の「構造」が合っているかは EXPECTED_PAYLOAD_V で見る。これが本当の互換性チェックで、
# Pine の JSON の形を変えたときだけ上がる（Render 側のコードも同時に差し替わる）。
#
# Pine の版（range_v7.14 など）は記録するだけで、既定では照合しない。
# パラメータの既定値を変えただけでも上がる番号なので、配信を止める基準にすると
# Pine を直すたびに Render の環境変数も直さねばならず、事故のほうが増える。
# どうしても固定したいときだけ EXPECTED_PINE_VERSION に値を入れる（不一致は「注意」止まり）。
EXPECTED_PINE_VERSION = _str("EXPECTED_PINE_VERSION", "")
EXPECTED_PAYLOAD_V = 3

# ---- 保存先 ----
# TURSO_DATABASE_URL が libsql:// または https:// なら Turso（HTTP API）。空ならローカル SQLite（開発用）
TURSO_DATABASE_URL = _str("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN = _str("TURSO_AUTH_TOKEN", "")
SQLITE_PATH = _str("SQLITE_PATH", "signals.db")

# ---- Discord ----
DISCORD_WEBHOOK_URL_REALTIME = _str("DISCORD_WEBHOOK_URL_REALTIME", "")
DISCORD_REALTIME_IS_FORUM = _bool("DISCORD_REALTIME_IS_FORUM", True)
DISCORD_WEBHOOK_URL_DIGEST = _str("DISCORD_WEBHOOK_URL_DIGEST", "")
DISCORD_DIGEST_IS_FORUM = _bool("DISCORD_DIGEST_IS_FORUM", True)
DISCORD_WEBHOOK_URL_EXIT = _str("DISCORD_WEBHOOK_URL_EXIT", "")         # 手仕舞いの合図（空なら即時と同じ Webhook。銘柄のスレッドに追記する）
DISCORD_WEBHOOK_URL_ERROR = _str("DISCORD_WEBHOOK_URL_ERROR", "")
DISCORD_MAX_RETRIES = _int("DISCORD_MAX_RETRIES", 5)
DISCORD_BASE_DELAY_SECONDS = _float("DISCORD_BASE_DELAY_SECONDS", 0.8)
DISCORD_SEND_SPACING_SECONDS = _float("DISCORD_SEND_SPACING_SECONDS", 0.35)

# ---- OpenAI（まとめ配信の上位数件だけに使う）----
OPENAI_API_KEY = _str("OPENAI_API_KEY", "")
OPENAI_MODEL = _str("OPENAI_MODEL", "gpt-5.6-terra")
AI_TOP_N = _int("AI_TOP_N", 5)
AI_ON_REALTIME = _bool("AI_ON_REALTIME", True)
# 即時通知と引け後まとめで、モデルと考える深さを分けられる。
# 即時は1日数件なので厚く、まとめは件数が出るので薄く、が既定の考え方。
AI_MODEL_REALTIME = _str("AI_MODEL_REALTIME", OPENAI_MODEL)
AI_MODEL_DIGEST = _str("AI_MODEL_DIGEST", OPENAI_MODEL)
AI_EFFORT_REALTIME = _str("AI_EFFORT_REALTIME", "medium")   # none / low / medium / high / xhigh / max（gpt-6-astra は none 不可）
AI_EFFORT_DIGEST = _str("AI_EFFORT_DIGEST", "low")
AI_MAX_OUTPUT_TOKENS = _int("AI_MAX_OUTPUT_TOKENS", 700)
# チャート画像も渡すか（即時のみ。画像が作れなかったときは自動で文字だけになる）
AI_WITH_CHART = _bool("AI_WITH_CHART", True)
AI_IMAGE_DETAIL = _str("AI_IMAGE_DETAIL", "high")           # low / high / auto
# 枠の中の節目を AI にも渡すか。既定はオン。
# 渡すのは価格帯と、そこに重なっている目印の名前だけ。強弱の点数は渡さない
# （点数はこちらが決めた配点なので、事実として受け取られると断定を生みやすい）。
AI_WITH_LEVELS = _bool("AI_WITH_LEVELS", True)
LEVELS_ENABLED = _bool("LEVELS_ENABLED", True)               # Discord に節目を出すか

# ---- 配信の判断 ----
# 品質での絞り込みは既定で無し。Pine が発火したら通知する（覚える条件を1組にするため）。
# 通知が多いと感じたら、Pine の「必要な反応点」や「安定性の下限」を上げるほうが分かりやすい。
REALTIME_GRADES = [s.strip() for s in _str("REALTIME_GRADES", "A,B").split(",") if s.strip()]
# 土台の分類での絞り込みは既定で無し（第1層を判定に使わない方針に合わせる）。
# 絞りたくなったら環境変数で通す分類だけを並べる。
REALTIME_CLASSES = [s.strip() for s in _str("REALTIME_CLASSES", "uptrend_pause,bottoming,downtrend,directionless").split(",") if s.strip()]
GRADE_A_MIN_TOUCH_TOTAL = _int("GRADE_A_MIN_TOUCH_TOTAL", 4)
GRADE_A_MIN_STABILITY = _float("GRADE_A_MIN_STABILITY", 0.8)
NOTIFY_COOLDOWN_DAYS = _int("NOTIFY_COOLDOWN_DAYS", 1)      # 同一銘柄・同一方向の再通知間隔。1 は「同じ日に二度出さない」だけ
                                                            # （端に入り直さないと次の発火は起きないので、長く空ける必要がない）
RESET_POS_LOW = _float("RESET_POS_LOW", 40.0)
RESET_POS_HIGH = _float("RESET_POS_HIGH", 60.0)

# ---- チャート画像 ----
CHART_IMAGE_ENABLED = _bool("CHART_IMAGE_ENABLED", True)
CHART_BARS = _int("CHART_BARS", 120)
CHART_FONT_PATH = _str("CHART_FONT_PATH", "")   # 日本語フォント（.ttf/.otf）を置けば日本語ラベルになる
CHART_DATA_SOURCE = _str("CHART_DATA_SOURCE", "yahoo")
YAHOO_RANGE = _str("YAHOO_RANGE", "1y")

# ---- 再検証 ----
VERIFY_ENABLED = _bool("VERIFY_ENABLED", True)
VERIFY_POS_TOL = _float("VERIFY_POS_TOL", 4.0)      # 位置％の許容差
VERIFY_TOUCH_TOL = _int("VERIFY_TOUCH_TOL", 1)      # 反応点の許容差

# ---- まとめ配信 ----
SUMMARY_SECRET = _str("SUMMARY_SECRET", "")
# 銘柄マスタ（日本語の社名・市場・業種）。リポジトリに置いたファイルを読むのが既定。
# 非公開リポジトリの raw アドレスはトークン付きで期限切れになるため、URL は使わない。
STOCK_MASTER_CSV_PATH = _str("STOCK_MASTER_CSV_PATH", "data_j.csv")
STOCK_MASTER_CSV_URL = _str("STOCK_MASTER_CSV_URL", "")   # 公開 CSV を使いたいときだけ
STOCK_MASTER_CACHE_SECONDS = _int("STOCK_MASTER_CACHE_SECONDS", 3600)
MARKET_SYMBOL_MAIN = _str("MARKET_SYMBOL_MAIN", "NI225")   # 地合いの代表として記録に付ける銘柄
