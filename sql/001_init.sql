CREATE DATABASE IF NOT EXISTS tw_day_trading_lab;
USE tw_day_trading_lab;

CREATE TABLE IF NOT EXISTS fetch_ledger (
  dataset VARCHAR(64) NOT NULL,
  trading_date DATE NOT NULL,
  stock_id VARCHAR(16) NOT NULL,
  source VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL,
  request_count INT NOT NULL DEFAULT 1,
  error_message TEXT NULL,
  fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (dataset, trading_date, stock_id, source)
);

CREATE TABLE IF NOT EXISTS candidate_runs (
  run_id VARCHAR(64) PRIMARY KEY,
  trading_date DATE NOT NULL,
  source VARCHAR(64) NOT NULL,
  generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  status VARCHAR(32) NOT NULL,
  notes TEXT NULL
);

CREATE TABLE IF NOT EXISTS candidate_items (
  run_id VARCHAR(64) NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  name VARCHAR(64) NOT NULL,
  rank_no INT NOT NULL,
  archetype VARCHAR(64) NOT NULL,
  total_score DECIMAL(8, 4) NOT NULL,
  next_day_actionable BOOLEAN NOT NULL DEFAULT FALSE,
  reasons JSON NULL,
  downgrade_reasons JSON NULL,
  PRIMARY KEY (run_id, symbol)
);

CREATE TABLE IF NOT EXISTS valid_samples (
  sample_id VARCHAR(96) PRIMARY KEY,
  trading_date DATE NOT NULL,
  source VARCHAR(32) NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  candidate_source VARCHAR(64) NOT NULL,
  archetype VARCHAR(64) NOT NULL,
  strategy_id VARCHAR(64) NOT NULL,
  setup_id VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(160) NOT NULL,
  entry_ts DATETIME NULL,
  entry_price DECIMAL(12, 4) NULL,
  exit_ts DATETIME NULL,
  exit_price DECIMAL(12, 4) NULL,
  exit_reason VARCHAR(64) NULL,
  stop_price DECIMAL(12, 4) NULL,
  mfe_r DECIMAL(10, 4) NULL,
  mae_r DECIMAL(10, 4) NULL,
  realized_r_gross DECIMAL(10, 4) NULL,
  estimated_cost_r DECIMAL(10, 4) NULL,
  realized_r_net DECIMAL(10, 4) NULL,
  validity VARCHAR(32) NOT NULL,
  exclusion_reason VARCHAR(128) NULL,
  position_id VARCHAR(96) NULL,
  event_count INT NOT NULL DEFAULT 0,
  warnings JSON NULL,
  KEY idx_sample_idempotency (idempotency_key)
);

CREATE TABLE IF NOT EXISTS order_intents (
  idempotency_key VARCHAR(160) PRIMARY KEY,
  trading_date DATE NOT NULL,
  strategy_id VARCHAR(64) NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  setup_id VARCHAR(64) NOT NULL,
  side VARCHAR(8) NOT NULL,
  status VARCHAR(32) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
