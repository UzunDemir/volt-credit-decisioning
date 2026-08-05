"""Portfolio forecasting — monthly cohort default rate with Holt-Winters.

Why this matters for the business: the scoring model decides per-application,
but the *portfolio* default rate drives provisioning, pricing and capital
planning. This module forecasts the 6-month trajectory of the cohort default
rate so finance can plan — a forecasting story on top of the classification
model.

Methodology:
  * series: monthly default rate by application cohort (labeled history)
  * model: Holt-Winters additive (trend + seasonality) via statsmodels
  * validation: backtest on the last 6 months, MAPE reported
  * output: forecast chart + table logged to MLflow

Usage:
    python -m credit_decision.model.forecast
"""

from __future__ import annotations

import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from ..config import get_settings
from ..db import read_sql

HORIZON = 6
BACKTEST_MONTHS = 6


def build_monthly_series() -> pd.DataFrame:
    """Default rate + volume by application month (labeled cohort)."""
    df = read_sql(
        """
        SELECT date_trunc('month', applied_at)::date AS cohort_month,
               count(*)::int                              AS n_apps,
               avg(has_default_12m::int)                   AS default_rate
        FROM applications
        WHERE has_default_12m IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """
    )
    df["cohort_month"] = pd.to_datetime(df["cohort_month"])
    return df


def fit_forecast(series: pd.Series, horizon: int = HORIZON) -> tuple[pd.Series, float, object]:
    """Holt-Winters with backtest MAPE on the tail of the series."""
    train = series.iloc[:-BACKTEST_MONTHS]
    backtest = series.iloc[-BACKTEST_MONTHS:]
    mape = float("nan")
    if len(train) >= 8:
        model = ExponentialSmoothing(
            train, trend="add", seasonal="add", seasonal_periods=12,
            initialization_method="estimated",
        ).fit()
        pred = model.forecast(len(backtest))
        mape = float((abs((backtest - pred) / backtest)).mean())
        model_full = ExponentialSmoothing(
            series, trend="add", seasonal="add", seasonal_periods=12,
            initialization_method="estimated",
        ).fit()
        return model_full.forecast(horizon), mape, model_full
    model = ExponentialSmoothing(series, trend="add", seasonal=None).fit()
    return model.forecast(horizon), mape, model


def main() -> None:
    s = get_settings()
    t0 = time.time()
    mlflow.set_tracking_uri(s.mlflow_tracking_uri)

    df = build_monthly_series()
    series = df.set_index("cohort_month")["default_rate"]
    print(f"series: {len(series)} monthly cohorts, last: {series.index[-1].date()}")

    forecast, mape, _ = fit_forecast(series)

    with mlflow.start_run(run_name="portfolio_forecast"):
        mlflow.log_metric("backtest_mape", mape if mape == mape else -1.0)

        fig, ax = plt.subplots(figsize=(10, 4.5))
        ax.plot(series.index, series.values, marker="o", ms=3, label="observed")
        fc_index = pd.date_range(series.index[-1] + pd.offsets.MonthBegin(1), periods=HORIZON, freq="MS")
        ax.plot(fc_index, forecast.values, marker="s", ms=3, label="forecast")
        ax.set_title(f"Portfolio default rate forecast (backtest MAPE={mape:.1%})")
        ax.set_ylabel("cohort default rate")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig("forecast.png", dpi=110)
        mlflow.log_artifact("forecast.png")

        table = pd.DataFrame({"month": fc_index, "forecast_default_rate": forecast.values})
        table.to_csv("forecast.csv", index=False)
        mlflow.log_artifact("forecast.csv")
        print(table.to_string(index=False))

    print(f"Forecast finished in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
