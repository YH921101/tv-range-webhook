# レンジ通知 v1 — TradingView → Render → Discord

Pine v7（`range_v7.pine`）が引け後に送る JSON を受け取り、保存（Turso）・再検証・チャート画像・Discord 配信を行う。
追跡と月次集計は GitHub Actions が回す。設計の背景は要件定義（Artifact「レンジ通知 要件定義」）を参照。

```
main.py                 受け口（Starlette）。/webhook/tradingview, /webhook/market, /summary/*, /ops/*, /export/*
app/
  config.py             環境変数
  db.py                 Turso（HTTP API を直接）／ローカル SQLite
  schema.sql            表定義（起動時に自動で作る）
  payload.py            JSON の検証と平坦化
  indicators.py         Pine v7 と同じ計算（回帰チャネル・反応点・ATR・土台の分類）
  verify.py             Yahoo の日足から計算し直して食い違いを記録
  evidence.py           上端・下端の根拠（キリ番・MA・実体高安・出来高帯）
  chart.py              画像（チャネル・ゾーン・反応点・MA・出来高）
  discord.py            送信（再試行・間隔・フォーラム・添付・分割）
  ai.py                 AI コメント（4行固定、失敗時は定型文）
  formatting.py         文面
  market.py             銘柄マスタ・地合い
  pipeline.py           受信 → 保存 → 再検証 → 画像 → 配信
  summaries.py          引け後まとめ・寄り前・月次・受信件数の照合
jobs/track.py           追跡の更新（GitHub Actions から直接 Turso へ）
.github/workflows/      起こし・引け後・追跡・寄り前・月次
market_watch.pine       地合いウォッチャー（指数用の小さな Pine）
tests/test_local.py     ローカルで一通り動かす確認
```

## 1. 保存先（Turso）

1. https://turso.tech でデータベースを作る（無料枠でよい）
2. `libsql://xxx.turso.io` の URL と、読み書きできるトークンを控える
3. 表は Render の起動時に `schema.sql` から自動で作られる。手で作るなら Turso のシェルに `schema.sql` を流す

## 2. Render

1. このフォルダを GitHub に置き、Render で Web Service（Python、無料枠）を作る
2. Build: `pip install -r requirements.txt`　Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
3. 環境変数（下の表）を設定する。`SUMMARY_SECRET` は長いランダム文字列
4. `https://<service>.onrender.com/` を開いて `"status": "ok"` と `"db": "turso"` を確認

| 変数 | 内容 |
|---|---|
| TURSO_DATABASE_URL / TURSO_AUTH_TOKEN | 保存先。空ならローカル SQLite（開発用。Render では消える） |
| EXPECTED_PINE_VERSION | 既定は空＝照合しない（Pine の版は記録だけ）。互換性は JSON 内の `v`（`EXPECTED_PAYLOAD_V`）で見る |
| DISCORD_WEBHOOK_URL_REALTIME | 即時通知（フォーラムチャンネル推奨。`DISCORD_REALTIME_IS_FORUM=true`） |
| DISCORD_WEBHOOK_URL_DIGEST | 引け後まとめ・寄り前・月次（フォーラム推奨） |
| DISCORD_WEBHOOK_URL_EXIT | 手仕舞いの合図。空なら即時と同じ Webhook で銘柄のスレッドに追記（任意） |
| DISCORD_WEBHOOK_URL_ERROR | 検証エラー・受信欠損（通常チャンネル） |
| OPENAI_API_KEY / OPENAI_MODEL | AI コメント。まとめ配信の上位 `AI_TOP_N` 件だけに付く。空なら定型文 |
| SUMMARY_SECRET | まとめ配信・CSV・受信照合の呼び出しに必要 |
| REALTIME_GRADES / REALTIME_CLASSES | 即時に流す品質（既定 A）と土台（既定 上昇中・底値圏・下落中） |
| GRADE_A_MIN_TOUCH_TOTAL / GRADE_A_MIN_STABILITY | 品質 A の条件（既定 反応点合計 4、安定性 0.8） |
| NOTIFY_COOLDOWN_DAYS | 同一銘柄・同一方向の再通知間隔（既定 7日。位置％が 40〜60％ に戻れば解除） |
| STOCK_MASTER_CSV_URL | `symbol,name,market,sector` の CSV（任意） |
| CHART_FONT_PATH | 日本語フォントのパス（任意。無ければ画像の文字は英数字のみ） |
| VERIFY_ENABLED / CHART_IMAGE_ENABLED | 再検証と画像の on/off |
| LEVELS_ENABLED | 枠の中の節目を Discord に出すか。既定 true |
| AI_WITH_LEVELS | 枠の中の節目を AI にも渡すか。既定 false（まず Discord だけに出して様子を見る） |

## 3. Discord

- フォーラムチャンネルを二つ（即時・まとめ）、通常チャンネルを三つ（呼び出し・地合い・エラー）作り、それぞれの Webhook URL を環境変数へ
- フォーラムでは1件1スレッドになり、画像が添付される

## 4. TradingView

1. `range_v7.pine` を Pine エディタに貼って保存し、日足チャートに載せる
2. アラート作成 → 条件「Range v7」→「alert() 関数の呼び出しすべて」→ Webhook URL に `https://<service>.onrender.com/webhook/tradingview`
   メッセージ欄は空でよい（本文は Pine が JSON を渡す）。有効期限は「無期限」にする
3. これを監視銘柄ぶん繰り返す（Premium はテクニカルアラート 400本）
4. 地合いは `market_watch.pine` を日経平均・TOPIX・グロース250・半導体などの 60分足に載せ、Webhook を `/webhook/market` にする

送信モードは Pine の入力「送信」で切り替える。まず「シグナル時のみ」で数日、受信ログを見てから「毎日1行（全銘柄の記録）」へ。

## 5. GitHub Actions

Secrets: `RENDER_URL`（`https://<service>.onrender.com`）、`SUMMARY_SECRET`、`TURSO_DATABASE_URL`、`TURSO_AUTH_TOKEN`
Variables: `EXPECTED_DAILY_RECEIVES`（毎日1行モードなら銘柄数。シグナル時のみなら 0）

| ワークフロー | JST | 内容 |
|---|---|---|
| keepalive | 14:50〜15:44 の2分おき | Render を起こしておく |
| close | 15:45 / 15:50 | 受信件数の照合 / 引け後まとめ |
| track | 16:30 | 追跡の更新（Turso へ直接） |
| morning | 08:30 | 寄り前 |
| monthly | 1日 09:00 | 月次集計 |

GitHub の定期実行は数分遅れることがある。起こしておく役だけは cron-job.org などの外部サービスに置き換えても良い。

## 6. ローカルで確認

```
pip install -r requirements.txt
python -m tests.test_local        # 合成データで受信→保存→再検証→画像→まとめ→追跡まで通す
uvicorn main:app --reload         # http://127.0.0.1:8000/
```

## 7. 運用メモ

- 受信は先に 200 を返して裏で処理する。保存が最初、画像・AI・Discord は方式 C が出た行だけ
- 再検証（`verify_status`）が `mismatch` の行は通知に「⚠」が付く。Pine と Render の版ずれ、Yahoo のデータずれを疑う
- Yahoo が取れない日は画像と再検証を省略し、通知は止めない
- 同じ `signal_id` が二度来ても一行のまま。配信状態や追跡は上書きされない
- CSV: `/export/signals.csv?secret=...`（追跡の結果も横に付く）
