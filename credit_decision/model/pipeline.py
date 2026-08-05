"""Feature contract + shared preprocessing pipeline.

The feature contract mirrors the SQL view ``v_credit_features``
(sql/02_features.sql). The training pipeline, the serving API and the
monitoring job all consume this exact column set — one source of truth.
"""

from __future__ import annotations

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ..db import read_sql

TARGET = "has_default_12m"
ID_COLUMNS = ["application_id", "client_id"]

FEATURE_COLUMNS_NUMERIC: list[str] = [
    "tenure_days", "amount", "term_months", "income", "age",
    "credit_history_months", "num_open_loans", "income_missing",
    "out_cnt_30d", "out_sum_30d", "out_cnt_90d", "out_sum_90d",
    "out_cnt_180d", "out_sum_180d", "in_cnt_90d", "in_sum_90d",
    "n_categories_90d", "n_merchants_90d", "night_out_cnt_90d",
    "mobile_out_cnt_90d", "n_cities_180d",
    "util_income_30d", "spend_trend_90_30", "night_share_90d",
    "mobile_share_90d", "inflow_coverage_90d",
]

FEATURE_COLUMNS_CATEGORICAL: list[str] = ["employment_status", "purpose"]

FEATURE_COLUMNS: list[str] = FEATURE_COLUMNS_NUMERIC + FEATURE_COLUMNS_CATEGORICAL


def build_preprocessor() -> ColumnTransformer:
    """Numeric: median impute + scale. Categorical: mode impute + one-hot."""
    numeric = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    categorical = Pipeline([
        ("impute", SimpleImputer(strategy="most_frequent")),
        ("onehot", OneHotEncoder(handle_unknown="ignore")),
    ])
    return ColumnTransformer([
        ("num", numeric, FEATURE_COLUMNS_NUMERIC),
        ("cat", categorical, FEATURE_COLUMNS_CATEGORICAL),
    ])


def load_training_data() -> pd.DataFrame:
    """Labeled rows from the SQL feature view."""
    return read_sql(
        "SELECT * FROM v_credit_features WHERE has_default_12m IS NOT NULL ORDER BY applied_at"
    )


def split_features_target(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    X = df[FEATURE_COLUMNS]
    y = df[TARGET].astype(int)
    return X, y
