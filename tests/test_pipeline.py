"""Contract tests for the feature pipeline (no database required)."""

from __future__ import annotations

from credit_decision.model.pipeline import (
    FEATURE_COLUMNS,
    FEATURE_COLUMNS_CATEGORICAL,
    FEATURE_COLUMNS_NUMERIC,
    TARGET,
)


def test_feature_contract_no_duplicates():
    assert len(FEATURE_COLUMNS) == len(set(FEATURE_COLUMNS))
    assert len(FEATURE_COLUMNS) == len(FEATURE_COLUMNS_NUMERIC) + len(FEATURE_COLUMNS_CATEGORICAL)


def test_feature_contract_core_columns_present():
    for col in ("income", "out_sum_30d", "util_income_30d", "night_share_90d", "employment_status", "purpose"):
        assert col in FEATURE_COLUMNS


def test_target_constant():
    assert TARGET == "has_default_12m"
