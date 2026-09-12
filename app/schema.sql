-- =====================================================
-- レンジ通知 保存先（Turso / libSQL = SQLite 互換）
-- 方針：
--   ・signals は「1シグナル（または1日1行の記録）＝1行」。判定に使った数字を全部持つ
--   ・payload_json に受信 JSON を丸ごと残す（旧 Render と同じ。あとから列を足せる）
--   ・追跡は tracking に1行。定時処理が埋める
--   ・まとめ配信の二重送信防止は summary_sent（旧 Render を踏襲）
-- =====================================================

PRAGMA journal_mode = WAL;

-- -----------------------------------------------------
-- 1. 発生時の記録
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    signal_id        TEXT PRIMARY KEY,           -- 例 7203_20260909_LONG / 7203_20260909_SNAP
    received_at      TEXT NOT NULL,              -- ISO8601 JST
    bar_date         TEXT NOT NULL,              -- YYYY-MM-DD
    mode             TEXT NOT NULL,              -- signal / snapshot
    pine_version     TEXT,
    symbol           TEXT NOT NULL,
    name             TEXT,
    exchange         TEXT,
    tickerid         TEXT,
    tv_url           TEXT,
    tf               TEXT,
    currency         TEXT,

    -- 価格・出来高
    price            REAL,
    open             REAL,
    high             REAL,
    low              REAL,
    prev_close       REAL,
    change_pct       REAL,
    volume           REAL,
    vol_ratio_1      REAL,
    vol_ratio_20     REAL,
    atr              REAL,

    -- チャネル（方式C＝方式Bの枠）
    ch_len           INTEGER,
    width_type       TEXT,
    ch_upper         REAL,
    ch_lower         REAL,
    ch_mid           REAL,
    ch_slope         REAL,
    width_pct        REAL,
    width_atr        REAL,
    pos_pct          REAL,

    -- 第1層・第2層の数字
    slope_atr        REAL,
    eff_ratio        REAL,
    touch_up         INTEGER,
    touch_dn         INTEGER,
    stability        REAL,
    bull_bar         INTEGER,
    upper_half       INTEGER,
    gap_pct          REAL,
    event_bar        INTEGER,
    vol_spike        INTEGER,

    -- 土台
    base_class       TEXT,                       -- uptrend_pause / bottoming / directionless / downtrend
    above_ma100      INTEGER,
    ma5 REAL, ma10 REAL, ma20 REAL, ma50 REAL, ma100 REAL,
    ma20_chg5_pct    REAL,
    ma100_chg20_pct  REAL,
    w_ma5_chg1_pct   REAL,
    w_ma10_chg1_pct  REAL,

    -- 通過判定
    gate_l1_flat     INTEGER,
    gate_l1_base     INTEGER,
    gate_l2_width    INTEGER,
    gate_l2_stab     INTEGER,
    gate_l2_touch_buy  INTEGER,
    gate_l2_touch_sell INTEGER,
    confirm_buy      INTEGER,
    confirm_sell     INTEGER,
    can_fade         INTEGER,
    fail_reason      TEXT,

    -- 方式C の結果
    c_fired          INTEGER NOT NULL DEFAULT 0,
    c_side           TEXT,                       -- LONG / SHORT / ''
    c_kind           TEXT,                       -- entry / deep / spring / ''
    break_up         INTEGER,
    break_down       INTEGER,

    -- 影（方式A/B）
    a_fired          INTEGER, a_side TEXT, a_pos_pct REAL, a_upper REAL, a_lower REAL,
    b_fired          INTEGER, b_side TEXT, b_pos_pct REAL,

    -- 根拠（チャネル端の近くにある節目）
    round_up REAL, round_down REAL,
    body_high_20 REAL, body_high_40 REAL, body_high_60 REAL,
    body_low_20 REAL,  body_low_40 REAL,  body_low_60 REAL,
    vz1_low REAL, vz1_high REAL, vz1_ratio REAL,
    vz2_low REAL, vz2_high REAL, vz2_ratio REAL,
    vz3_low REAL, vz3_high REAL, vz3_ratio REAL,
    upper_reason     TEXT,                       -- Render が組み立てる「上端の根拠」
    lower_reason     TEXT,                       -- 「下端の根拠」

    -- 銘柄属性・地合い（Render が付ける）
    market           TEXT,
    sector           TEXT,

    -- Render 側の再検証（Yahoo 日足から再計算）
    verify_status    TEXT,                       -- match / mismatch / skipped
    verify_json      TEXT,

    -- 配信
    quality_grade    TEXT,
    confidence       TEXT,                       -- 強 / 中 / 弱（買う側の端の確かさ。加点のみ）
    confidence_score REAL,                       -- その合計点（0〜18目安）
    confidence_detail TEXT,                      -- 内訳の文章
    levels_json      TEXT,                       -- 枠の中の節目（参考情報。判定には使わない）
    delivered        TEXT NOT NULL DEFAULT 'none', -- none / realtime / digest / both
    delivered_at     TEXT,
    discord_thread_url TEXT,
    ai_comment       TEXT,

    -- 生データ
    payload_json     TEXT NOT NULL,
    metrics_json     TEXT,
    cautions         TEXT,
    ai_model         TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_date      ON signals(bar_date);
CREATE INDEX IF NOT EXISTS idx_signals_symbol    ON signals(symbol, bar_date);
CREATE INDEX IF NOT EXISTS idx_signals_fired     ON signals(c_fired, bar_date);
CREATE INDEX IF NOT EXISTS idx_signals_class     ON signals(base_class, bar_date);

-- -----------------------------------------------------
-- 2. その後の追跡（定時処理が埋める。1シグナル1行）
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS tracking (
    signal_id        TEXT PRIMARY KEY REFERENCES signals(signal_id),
    entry_price      REAL NOT NULL,              -- 発生日の終値（第1段階）。翌日寄りに変える案あり
    side             TEXT NOT NULL,
    target_mid       REAL,
    target_upper     REAL,
    stop_lower       REAL,

    d1_close REAL, d3_close REAL, d5_close REAL, d10_close REAL, d20_close REAL,
    d1_ret_pct REAL, d3_ret_pct REAL, d5_ret_pct REAL, d10_ret_pct REAL, d20_ret_pct REAL,
    max_up_pct_20    REAL,                       -- 20営業日内の最大上昇（買いなら含み益の最大）
    max_dn_pct_20    REAL,                       -- 20営業日内の最大下落（含み損の深さ）

    hit_mid_day      INTEGER,                    -- 中心に何日で到達したか（null=未到達）
    hit_target_day   INTEGER,                    -- 反対側の端に何日で到達したか
    broke_stop_day   INTEGER,                    -- 下端割れ（陰線2本連続）が何日目に起きたか
    first_event      TEXT,                       -- target / mid / stop / none  最初に起きた事象
    outcome          TEXT,                       -- win / loss / flat / open

    last_tracked_date TEXT,
    days_tracked     INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'open', -- open / done
    notified_exits   TEXT,                       -- 出口の合図をどれだけ通知したか（mid,target,stop）
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tracking_status ON tracking(status);

-- -----------------------------------------------------
-- 3. まとめ配信の二重送信防止（旧 Render を踏襲）
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS summary_sent (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_type  TEXT NOT NULL,                 -- close_digest / morning / monthly
    target_date   TEXT NOT NULL,
    sent_at       TEXT NOT NULL,
    row_count     INTEGER NOT NULL,
    UNIQUE(summary_type, target_date)
);

-- -----------------------------------------------------
-- 4. 再通知の抑制状態（同一銘柄・同一方向は N 営業日空ける）
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS notify_state (
    symbol             TEXT NOT NULL,
    side               TEXT NOT NULL,
    last_notified_date TEXT,
    reset_armed        INTEGER NOT NULL DEFAULT 0, -- 位置％が中心まで戻ったら 1（再通知可）
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (symbol, side)
);

-- -----------------------------------------------------
-- 5. 銘柄属性・決算日（手作業更新から始めてよい）
-- -----------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_master (
    symbol      TEXT PRIMARY KEY,
    name        TEXT,
    market      TEXT,                            -- プライム / スタンダード / グロース
    sector      TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS receive_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at  TEXT NOT NULL,
    symbol       TEXT,
    signal_id    TEXT,
    ok           INTEGER NOT NULL,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_receive_log_time ON receive_log(received_at);

-- -----------------------------------------------------
-- 集計の例（月次で Discord に流すもの）
-- -----------------------------------------------------
-- 方式別の到達率と所要日数：
--   SELECT s.base_class, s.c_kind,
--          COUNT(*)                                        AS n,
--          AVG(CASE WHEN t.hit_target_day IS NOT NULL THEN 1.0 ELSE 0 END) AS reach_rate,
--          AVG(t.hit_target_day)                           AS avg_days,
--          AVG(t.d10_ret_pct)                              AS avg_ret_10d,
--          AVG(t.max_dn_pct_20)                            AS avg_drawdown
--   FROM signals s JOIN tracking t USING(signal_id)
--   WHERE s.c_fired = 1 AND t.status = 'done'
--   GROUP BY s.base_class, s.c_kind;
--
-- 「閾値を変えたらどうなるか」の再計算（Pine を触らずに）：
--   SELECT ... FROM signals WHERE mode='snapshot' AND pos_pct <= 10 AND touch_dn >= 2 ...

-- -----------------------------------------------------
-- 7. 地合いウォッチャー（指数の状態。別 Pine から届く）
-- -----------------------------------------------------
