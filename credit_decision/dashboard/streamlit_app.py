"""Standalone Streamlit demo — no database, no API.

A self-contained showcase of the Volt Credit Decisioning platform for
Streamlit Community Cloud: KPIs, methodology, drift story and an
illustrative scoring calculator. All numbers are the real outputs of the
full platform (docker compose); the full version with live PostgreSQL and
the serving API lives in credit_decision/dashboard/app.py.

Deploy: pick this file as the main app; requirements-dashboard.txt.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

LOGO = Path(__file__).resolve().parent / "credit_decision" / "dashboard" / "assets" / "volt_logo.png"

st.set_page_config(
    page_title="Volt Credit Decisioning — demo",
    page_icon=str(LOGO) if LOGO.exists() else "📊",
    layout="wide",
)

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    if LOGO.exists():
        st.image(str(LOGO), width=90)
    st.markdown("## Volt Credit Decisioning")
    st.markdown(
        "Standalone demo of an end-to-end credit scoring platform: SQL features, "
        "MLflow-tracked training, cost-based threshold, Evidently drift monitoring, "
        "Grafana alerting, Airflow orchestration. No database or API needed here - "
        "the numbers below are real outputs of the full stack."
    )
    with st.expander("Methodology", expanded=True):
        st.markdown(
            """
            - **Time-based split** (train < 2025-07, val 07..09, test 10..12) - credit
              risk is non-stationary, random splits leak the future.
            - **5-fold CV** compares logistic / random forest / XGBoost.
            - **Isotonic calibration** wrapped into the served model (fit on val only).
            - **Threshold by business cost**: `FP*1.0 + FN*0.2` on validation;
              approve when `P(default) <= threshold`.
            - **Monitoring**: Evidently drift vs 2025-H2 reference; score-distribution
              drift as early proxy (labels lag 12 months).
            """
        )

# ------------------------------------------------------------------ header
st.title("Volt Credit Decisioning")
st.caption(
    "End-to-end ML platform demo: decisions, model, drift. "
    "Full stack runs via `docker compose up --build`."
)

# ------------------------------------------------------------------ KPIs
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Model version", "v11")
c2.metric("Threshold", "0.157")
c3.metric("Test ROC-AUC", "0.691")
c4.metric("Calibration (ECE)", "0.002")
c5.metric("Approval rate", "86.2%")

# ------------------------------------------------------------------ monitoring
st.subheader("Drift & data quality by production month")
months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]
share = [0.0, 0.0, 0.0, 0.310, 0.345, 0.345, 0.379]
detected = [s >= 0.3 for s in share]
mean_score = [0.088, 0.087, 0.089, 0.104, 0.112, 0.117, 0.124]

drift = pd.DataFrame({
    "batch_name": months, "share_drifted": share,
    "drift_detected": detected, "score_mean": mean_score,
})

c1, c2 = st.columns(2)
with c1:
    st.plotly_chart(
        px.bar(drift, x="batch_name", y="share_drifted", color="drift_detected",
               height=320, labels={"batch_name": "month", "share_drifted": "share of drifted columns"},
               color_discrete_map={True: "#c62828", False: "#2e7d32"}),
        use_container_width=True,
    )
with c2:
    st.plotly_chart(
        px.line(drift, x="batch_name", y="score_mean", height=320,
                labels={"batch_name": "month", "score_mean": "mean predicted default prob"}),
        use_container_width=True,
    )

st.dataframe(
    pd.DataFrame({
        "month": months,
        "drift_detected": detected,
        "share_drifted": share,
        "top drifted features": [
            "amount_ratio, income_ratio", "util_income_30d, income_ratio",
            "util_income_30d, out_cnt_30d", "income_ratio, util_income_30d",
            "util_income_30d, income_ratio", "util_income_30d, income_ratio",
            "util_income_30d, income_ratio",
        ],
    }),
    use_container_width=True, hide_index=True,
)

# ------------------------------------------------------------------ score demo
st.subheader("Illustrative scoring calculator")
st.caption(
    "Approximates the champion model (logistic, calibrated) with the real threshold. "
    "For demo only - the production API computes features from SQL."
)

emp_map = {"employed": 0.0, "self_employed": 0.35, "student": 0.55, "retired": 0.65, "unemployed": 1.0}
purpose_risk = {"personal": 0.0, "home_improvement": -0.1, "education": -0.05, "medical": 0.05,
                "vehicle": 0.1, "debt_consolidation": 0.35, "business": 0.3}

col1, col2, col3 = st.columns(3)
with col1:
    income = st.number_input("Monthly income ($)", min_value=0.0, value=3000.0, step=100.0)
    amount = st.number_input("Requested amount ($)", min_value=0.0, value=6000.0, step=500.0)
    term = st.selectbox("Term (months)", [6, 12, 24, 36, 48, 60], index=2)
with col2:
    age = st.slider("Age", 18, 78, 35)
    history = st.slider("Credit history (months)", 0, 420, 60)
    loans = st.slider("Open loans", 0, 8, 2)
with col3:
    employment = st.selectbox("Employment", list(emp_map))
    purpose = st.selectbox("Purpose", list(purpose_risk))

if income > 0 and amount > 0:
    util = amount / (income * term / 12.0)
    logit = (
        -2.9
        + 1.1 * emp_map[employment]
        + 1.0 * purpose_risk[purpose]
        + 0.9 * np.clip(util - 0.5, 0.0, 3.0)
        + 0.5 * np.clip(loans - 3, 0, 5)
        - 0.01 * history
        + 0.004 * np.clip(age - 50, 0, 28)
    )
    p_default = 1.0 / (1.0 + math.exp(-logit))
    approved = p_default <= 0.157
    st.markdown(
        f"**P(default) = {p_default:.4f}** vs threshold **0.157** "
        f"-> **{'APPROVE 🟢' if approved else 'DECLINE 🔴'}**"
    )

# ------------------------------------------------------------------ docs
with st.expander("How the full platform works"):
    st.markdown(
        """
        1. **ETL** - seeded generator: clients, applications (12-month default labels),
           JSONB transaction history; loaded via PostgreSQL COPY.
        2. **Features in SQL** - `v_credit_features`: rolling 30/90/180d windows, JSONB
           extraction, engineered ratios. One view for train/serve/monitor - no skew.
        3. **Training** - time-based split, 5-fold CV, isotonic calibration,
           cost-based threshold; registered to MLflow with the `production` alias.
        4. **Serving** - FastAPI reads the model from the registry, scores, writes
           the audit log to `decisions`.
        5. **Monitoring** - Evidently drift/quality per month vs 2025-H2 reference;
           the simulated downturn from 2026-04 makes alerts fire.
        6. **Observability & orchestration** - Grafana dashboards + alert rule
           (drift >= 30% -> webhook -> `alert_events`); Airflow DAGs:
           `daily_monitoring`, `weekly_retrain`, `monthly_forecast`.
        """
    )
