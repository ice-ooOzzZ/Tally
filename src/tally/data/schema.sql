-- Tally 存储 DDL —— M1 T1.1 范围（IMPLEMENTATION_SPEC.md §3.2）。
--
-- 本任务只建 kline / valuation / signals 三张表（对应 §3.2 全量 DDL 中的这三段）；
-- 剩余表（limit_prices/fundamentals_*/positions/... 等）留给 M2 T2.1 的"全量 DDL"任务，
-- 届时本文件会被替换为完整版并同步更新 §3.2。
--
-- 一期允许"删库重同步"（IMPLEMENTATION_SPEC.md §0）：DDL 变更无需迁移脚本，
-- Repository 启动时用 executescript 幂等建表（见下方 IF NOT EXISTS）。
--
-- 与 §3.2 原文的两处小出入（记录在 changelog，非语义变更）：
--   1. PK 列与 signals.advice / kline.source 补充 NOT NULL —— §3.2 原文未写但语义上
--      要求非空（PK 列本就不该为 NULL；advice/source 是有默认值/枚举语义的必填列）。
--   2. CREATE TABLE / CREATE INDEX 均加 IF NOT EXISTS —— 支撑"建表幂等"这条工程 AC
--      （多次对同一个库跑 executescript 不报错），不改变列定义或约束语义。

CREATE TABLE IF NOT EXISTS kline (            -- 指数复用本表, market ∈ {CN, US, INDEX}
  code TEXT NOT NULL,
  market TEXT NOT NULL,
  date TEXT NOT NULL,                         -- date = 交易所当地交易日 YYYY-MM-DD
  open REAL,
  high REAL,
  low REAL,
  close REAL,
  volume REAL,
  amount REAL,
  adj_factor REAL,                            -- CN: 不复权价+复权因子; US: 见 §3.1 #12
  source TEXT NOT NULL DEFAULT 'primary',     -- primary / stooq（降级补缺标记）
  PRIMARY KEY (code, market, date)
);
CREATE INDEX IF NOT EXISTS idx_kline_md ON kline(market, date);

CREATE TABLE IF NOT EXISTS valuation (
  code TEXT NOT NULL,
  market TEXT NOT NULL,
  date TEXT NOT NULL,
  pe_ttm REAL,
  pb REAL,
  market_cap REAL,
  turnover_amt REAL,
  PRIMARY KEY (code, market, date)
);

CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id TEXT NOT NULL,
  date TEXT NOT NULL,
  code TEXT NOT NULL,
  market TEXT NOT NULL,
  advice TEXT NOT NULL,                       -- buy / exit / avoid / hold
  score REAL,
  reasons_json TEXT,
  price_at_signal REAL,
  stop_loss REAL,
  position_pct REAL
);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date);
