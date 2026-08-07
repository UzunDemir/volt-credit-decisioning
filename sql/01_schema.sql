-- =====================================================================
-- Volt Credit Decisioning — schema (PostgreSQL 16)
-- Entities: clients, applications (with 12-month outcome label),
--           transactions (semi-structured JSONB), decisions (prod log),
--           monitoring_events (drift / data-quality summary).
-- =====================================================================

CREATE TABLE IF NOT EXISTS clients (
    client_id     BIGINT PRIMARY KEY,
    first_seen_at TIMESTAMP NOT NULL,
    region        VARCHAR(16) NOT NULL,
    segment       VARCHAR(16) NOT NULL DEFAULT 'existing'  -- 'new' | 'existing'
);

CREATE TABLE IF NOT EXISTS applications (
    application_id       BIGINT PRIMARY KEY,
    client_id            BIGINT NOT NULL REFERENCES clients(client_id),
    applied_at           TIMESTAMP NOT NULL,
    amount               NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    term_months          INT NOT NULL CHECK (term_months IN (6,12,24,36,48,60)),
    purpose              VARCHAR(64),
    income               NUMERIC(14,2),
    employment_status    VARCHAR(32),
    age                  INT,
    credit_history_months INT,
    num_open_loans       INT,
    -- label: client defaulted on any obligation within 12 months
    -- NULL for applications that are too recent to label (scoring batch)
    has_default_12m      BOOLEAN
);

CREATE INDEX IF NOT EXISTS ix_applications_applied_at ON applications (applied_at);
CREATE INDEX IF NOT EXISTS ix_applications_client_id   ON applications (client_id);
CREATE INDEX IF NOT EXISTS ix_applications_label       ON applications (has_default_12m) WHERE has_default_12m IS NOT NULL;

-- Semi-structured transaction data: fixed columns + free-form JSONB payload
-- (merchant metadata, device, geo, channel — everything the API layer may
--  extend without migrations).
CREATE TABLE IF NOT EXISTS transactions (
    txn_id    BIGINT PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES clients(client_id),
    txn_ts    TIMESTAMP NOT NULL,
    amount    NUMERIC(12,2) NOT NULL,
    direction VARCHAR(8) NOT NULL CHECK (direction IN ('in','out')),
    category  VARCHAR(64),
    details   JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_transactions_client_ts ON transactions (client_id, txn_ts);
CREATE INDEX IF NOT EXISTS ix_transactions_details   ON transactions USING GIN (details);

-- Production decision log (written by the serving API)
CREATE TABLE IF NOT EXISTS decisions (
    decision_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    application_id BIGINT NOT NULL,
    model_version  VARCHAR(64) NOT NULL,
    score          NUMERIC(8,6) NOT NULL,
    decision       VARCHAR(8) NOT NULL CHECK (decision IN ('approve','decline')),
    challenger_score NUMERIC(8,6),  -- shadow candidate probability (champion-challenger)
    decided_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_decisions_decided_at ON decisions (decided_at);

-- Alert webhook audit trail (written by the serving API POST /v1/alerts)
CREATE TABLE IF NOT EXISTS alert_events (
    event_id    BIGSERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    message     TEXT,
    state       TEXT,
    labels      JSONB,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Monitoring summary (written by the drift/quality job)
CREATE TABLE IF NOT EXISTS monitoring_events (
    event_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_name     VARCHAR(128) NOT NULL,
    report_type    VARCHAR(32) NOT NULL,   -- 'data_drift' | 'data_quality' | 'model_performance'
    drift_detected BOOLEAN,
    metrics        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);
