-- =====================================================================
-- 02_features.sql — production feature engineering lives in SQL.
-- One view, keyed by application_id, consumed by the training pipeline
-- and the serving API. Demonstrates:
--   * window/rolling aggregates over 30/90/180-day lookbacks
--   * JSONB extraction (merchant, channel, geo)
--   * engineered ratios (utilization, spend trend, night/mobile share)
--   * missing-value flags
-- =====================================================================

CREATE OR REPLACE VIEW v_credit_features AS
WITH pre_app AS (
    -- training rows (labeled) + scoring rows (post-2026, unlabeled)
    SELECT application_id, client_id, applied_at
    FROM applications
    WHERE has_default_12m IS NOT NULL
       OR applied_at >= '2026-01-01'
),
agg AS (
    SELECT
        p.application_id,
        p.applied_at,
        -- ---- rolling spend, 30 / 90 / 180 days ---------------------
        count(*)   FILTER (WHERE t.direction = 'out'
                           AND t.txn_ts >= p.applied_at - interval '30 days')  AS out_cnt_30d,
        coalesce(sum(t.amount) FILTER (WHERE t.direction = 'out'
                           AND t.txn_ts >= p.applied_at - interval '30 days'), 0) AS out_sum_30d,
        count(*)   FILTER (WHERE t.direction = 'out'
                           AND t.txn_ts >= p.applied_at - interval '90 days')  AS out_cnt_90d,
        coalesce(sum(t.amount) FILTER (WHERE t.direction = 'out'
                           AND t.txn_ts >= p.applied_at - interval '90 days'), 0) AS out_sum_90d,
        count(*)   FILTER (WHERE t.direction = 'out'
                           AND t.txn_ts >= p.applied_at - interval '180 days') AS out_cnt_180d,
        coalesce(sum(t.amount) FILTER (WHERE t.direction = 'out'
                           AND t.txn_ts >= p.applied_at - interval '180 days'), 0) AS out_sum_180d,
        -- ---- inflows ------------------------------------------------
        count(*)   FILTER (WHERE t.direction = 'in'
                           AND t.txn_ts >= p.applied_at - interval '90 days')  AS in_cnt_90d,
        coalesce(sum(t.amount) FILTER (WHERE t.direction = 'in'
                           AND t.txn_ts >= p.applied_at - interval '90 days'), 0) AS in_sum_90d,
        -- ---- diversity (categories / merchants) ---------------------
        count(DISTINCT t.category)                    FILTER (WHERE t.txn_ts >= p.applied_at - interval '90 days')  AS n_categories_90d,
        count(DISTINCT t.details->>'merchant')        FILTER (WHERE t.txn_ts >= p.applied_at - interval '90 days')  AS n_merchants_90d,
        -- ---- JSONB-derived behaviour --------------------------------
        count(*)   FILTER (WHERE t.direction = 'out' AND t.txn_ts >= p.applied_at - interval '90 days'
                           AND (extract(hour FROM t.txn_ts) >= 23 OR extract(hour FROM t.txn_ts) < 6)) AS night_out_cnt_90d,
        count(*)   FILTER (WHERE t.direction = 'out' AND t.txn_ts >= p.applied_at - interval '90 days'
                           AND t.details->>'channel' = 'mobile') AS mobile_out_cnt_90d,
        count(DISTINCT t.details->'geo'->>'city')     FILTER (WHERE t.txn_ts >= p.applied_at - interval '180 days') AS n_cities_180d
    FROM pre_app p
    LEFT JOIN transactions t
           ON t.client_id = p.client_id
          AND t.txn_ts < p.applied_at
          AND t.txn_ts >= p.applied_at - interval '180 days'
    GROUP BY p.application_id, p.applied_at
)
SELECT
    a.application_id,
    a.client_id,
    a.applied_at,
    a.purpose,
    extract(epoch FROM (a.applied_at - c.first_seen_at)) / 86400.0 AS tenure_days,
    a.amount,
    a.term_months,
    a.income,
    a.employment_status,
    a.age,
    a.credit_history_months,
    a.num_open_loans,
    CASE WHEN a.income IS NULL THEN 1 ELSE 0 END AS income_missing,
    -- ---- rolling aggregates ----------------------------------------
    agg.out_cnt_30d, agg.out_sum_30d,
    agg.out_cnt_90d, agg.out_sum_90d,
    agg.out_cnt_180d, agg.out_sum_180d,
    agg.in_cnt_90d, agg.in_sum_90d,
    agg.n_categories_90d, agg.n_merchants_90d,
    agg.night_out_cnt_90d, agg.mobile_out_cnt_90d, agg.n_cities_180d,
    -- ---- engineered ratios -----------------------------------------
    agg.out_sum_30d / NULLIF(a.income / 12.0, 0)                AS util_income_30d,
    agg.out_sum_90d / NULLIF(agg.out_sum_30d, 0)                AS spend_trend_90_30,
    agg.night_out_cnt_90d / NULLIF(agg.out_cnt_90d, 0)          AS night_share_90d,
    agg.mobile_out_cnt_90d / NULLIF(agg.out_cnt_90d, 0)         AS mobile_share_90d,
    agg.in_sum_90d / NULLIF(agg.out_sum_90d, 0)                 AS inflow_coverage_90d,
    -- ---- label ------------------------------------------------------
    a.has_default_12m
FROM applications a
JOIN agg USING (application_id)
LEFT JOIN clients c USING (client_id);
