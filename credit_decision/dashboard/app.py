"""Streamlit business dashboard — for non-technical stakeholders.

Tabs:
  * Business overview — approvals, approval rate, score distribution
  * Model            — deployed version, test metrics, decision mix
  * Monitoring       — drift per production month (from monitoring_events)
  * Score demo       — try the live API on a real application id

Data sources: PostgreSQL (decisions, monitoring_events) + the serving API.
Run: streamlit run credit_decision/dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# streamlit runs this file as a script (not a package module), so relative
# imports would fail; put the repo root on sys.path and import absolutely
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

from credit_decision.config import get_settings
from credit_decision.db import read_sql

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "volt_logo.png"

st.set_page_config(
    page_title="Volt Credit Decisioning",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "📊",
    layout="wide",
)

SETTINGS = get_settings()
API = SETTINGS.api_url

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("## Volt Credit Decisioning")
    st.markdown(
        "End-to-end credit scoring platform: **SQL features -> MLflow-tracked "
        "training -> FastAPI serving -> Evidently monitoring -> Grafana alerting "
        "-> Airflow orchestration**. Data is synthetic and seeded - byte-identical "
        "on any machine."
    )

    with st.expander("Methodology", expanded=True):
        st.markdown(
            """
            - **Time-based split**: train < 2025-07, validation 2025-07..09, test
              2025-10..12. Credit risk is non-stationary - random splits leak the
              future and flatter AUC.
            - **Model selection**: logistic baseline vs random forest vs XGBoost,
              compared by honest 5-fold CV AUC on train.
            - **Calibration**: isotonic, wrapped INTO the served model
              (`CalibratedClassifierCV`, fit on validation only) - the threshold
              always sees calibrated probabilities.
            - **Threshold = business decision**: minimize `FP*1.0 + FN*0.2` on
              validation; approve when `P(default) <= threshold`.
            - **Monitoring**: Evidently `DataDriftPreset` + `DataSummaryPreset`
              per production month vs 2025-H2 reference; score-distribution drift
              as the early-warning proxy (labels lag 12 months).
            - **Experiments**: A/B sizing (Fleiss two-proportion) and uplift
              modelling (two-model, out-of-fold predictions).
            - **Forecasting**: Holt-Winters on the monthly cohort default rate,
              backtest MAPE logged to MLflow.
            """
        )

    with st.expander("How to run", expanded=True):
        st.markdown(
            """
            ```bash
            docker compose up --build
            # postgres -> etl -> train -> api (:8000) + dashboard (:8501)

            docker compose --profile full up -d
            # adds Grafana (:3000, admin/volt) and Airflow (:8080, admin/volt)

            # simulate production months + drift reports:
            docker compose run --no-deps --rm monitor \
                python -m credit_decision.monitoring.run_monitor --simulate
            ```
            """
        )

    with st.expander("Monitoring", expanded=True):
        st.markdown(
            """
            - This **Monitoring tab**: drift share by month, top drifted
              features, mean predicted score.
            - **Grafana** (http://localhost:3000): drift/approval dashboards;
              alert rule fires when `share_drifted >= 0.3` and delivers the
              alert via webhook to the API -> `alert_events` (closed loop).
            - **MLflow** (http://localhost:5000): runs, registry, `production`
              alias, threshold as a model tag.
            """
        )

    with st.expander("Airflow DAGs", expanded=True):
        st.markdown(
            """
            - `daily_monitoring` - every day 07:00: Evidently report on the
              latest production batch.
            - `weekly_retrain` - Monday 03:00: retrain champion + promote the
              `production` alias (drift-triggered in real prod).
            - `monthly_forecast` - 1st of month 04:00: portfolio default-rate
              forecast for finance.
            - UI: http://localhost:8080 (admin/volt).
            """
        )



@st.cache_data(ttl=30)
def load_decisions(limit: int = 20_000) -> pd.DataFrame:
    return read_sql(
        "SELECT application_id, model_version, score, decision, decided_at "
        "FROM decisions ORDER BY decision_id DESC LIMIT :limit",
        {"limit": limit},
    )


@st.cache_data(ttl=60)
def load_monitoring() -> pd.DataFrame:
    return read_sql(
        "SELECT batch_name, report_type, drift_detected, metrics, created_at "
        "FROM monitoring_events ORDER BY batch_name, report_type"
    )


@st.cache_data(ttl=60)
def api_model_info() -> dict:
    try:
        r = httpx.get(f"{API}/model-info", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001 — dashboard must not crash on API outage
        return {"error": str(exc)}


def kpi_row(info: dict, decisions: pd.DataFrame) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Model version", info.get("version", "n/a"))
    c2.metric("Threshold", f"{info.get('threshold', 'n/a')}")
    c3.metric("Test ROC-AUC", info.get("test_roc_auc", "n/a"))
    if not decisions.empty:
        c4.metric("Applications scored", f"{len(decisions):,}")
        c5.metric("Approval rate", f"{decisions['decision'].eq('approve').mean():.1%}")
    else:
        c4.metric("Applications scored", "0")
        c5.metric("Approval rate", "—")


col_logo, col_head = st.columns([1, 6])
if LOGO_PATH.exists():
    col_logo.image(str(LOGO_PATH), width=90)
with col_head:
    st.title("Volt Credit Decisioning")
    st.caption("ML credit scoring platform — decisions, model, drift. Data: synthetic, seeded, reproducible.")

info = api_model_info()
try:
    decisions = load_decisions()
except Exception as exc:  # noqa: BLE001 — dashboard degrades gracefully
    decisions = pd.DataFrame()
    st.warning(f"Database unreachable ({exc}). Data tabs will be empty.")
if "error" in info:
    st.warning(f"API unreachable ({info['error']}). Model metrics hidden — data tabs still work.")

tab_overview, tab_model, tab_monitoring, tab_demo, tab_docs = st.tabs(
    ["Business overview", "Model", "Monitoring", "Score demo", "Docs"]
)

# ---------------------------------------------------------------- overview
with tab_overview:
    kpi_row(info, decisions)
    if not decisions.empty:
        decisions["decided_at"] = pd.to_datetime(decisions["decided_at"])
        daily = decisions.groupby(decisions["decided_at"].dt.date).agg(
            n=("application_id", "count"),
            approval_rate=("decision", lambda s: s.eq("approve").mean()),
        ).reset_index()
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Applications scored per day")
            st.plotly_chart(px.bar(daily, x="decided_at", y="n", height=300), use_container_width=True)
        with c2:
            st.subheader("Score distribution")
            st.plotly_chart(
                px.histogram(decisions, x="score", color="decision", nbins=40, height=300,
                             color_discrete_map={"approve": "#2e7d32", "decline": "#c62828"}),
                use_container_width=True,
            )
    else:
        st.info("No decisions yet — score an application in the demo tab or via the API.")

# ------------------------------------------------------------------ model
with tab_model:
    st.subheader("Deployed model")
    if "error" in info:
        st.error("Model info unavailable — is the API running?")
    else:
        for k, v in info.items():
            st.write(f"- **{k}**: `{v}`")
    st.markdown(
        """
        **Methodology**
        - Time-based split: train < 2025-07, validation 2025-07..09, test 2025-10..12.
        - Baseline (logistic) vs random forest vs XGBoost, selected by 5-fold CV AUC.
        - Isotonic calibration fit on validation only.
        - Approval threshold chosen to minimize `FP × cost_fp + FN × cost_fn`
          on validation — the business decides the FP/FN trade-off.
        """
    )
    if not decisions.empty:
        st.subheader("Decision mix by model version")
        mix = decisions.groupby(["model_version", "decision"]).size().reset_index(name="n")
        st.plotly_chart(px.bar(mix, x="model_version", y="n", color="decision", barmode="group"),
                        use_container_width=True)

# ------------------------------------------------------------ monitoring
with tab_monitoring:
    st.subheader("Drift & data quality by production month")
    try:
        mon = load_monitoring()
    except Exception as exc:  # noqa: BLE001
        mon = pd.DataFrame()
        st.warning(f"Database unreachable ({exc}).")
    if mon.empty:
        st.info("No monitoring events yet — run `python -m credit_decision.monitoring.run_monitor --simulate`")
    else:
        drift = mon[mon["report_type"] == "data_drift"].copy()
        drift["share_drifted"] = drift["metrics"].apply(
            lambda m: (m.get("share_drifted") if isinstance(m, dict) else None)
        )
        drift["score_mean"] = drift["metrics"].apply(
            lambda m: (m.get("score", {}).get("mean") if isinstance(m, dict) else None)
        )
        drift["top_drifted"] = drift["metrics"].apply(
            lambda m: (", ".join(f"{k}:{v:.2f}" for k, v in m.get("top_drifted", []))
                       if isinstance(m, dict) and m.get("top_drifted") else None)
        )
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(
                px.bar(drift, x="batch_name", y="share_drifted",
                       color="drift_detected", height=320,
                       labels={"batch_name": "month", "share_drifted": "share of drifted columns"},
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
            drift[["batch_name", "drift_detected", "share_drifted", "top_drifted"]]
            .rename(columns={"top_drifted": "top drifted features"}),
            use_container_width=True, hide_index=True,
        )

# ------------------------------------------------------------- score demo
with tab_demo:
    st.subheader("Live scoring — try the API")
    try:
        sample_ids = read_sql(
            "SELECT application_id FROM applications WHERE applied_at >= '2026-01-01' "
            "ORDER BY application_id LIMIT 5"
        )["application_id"].tolist()
    except Exception:  # noqa: BLE001
        sample_ids = []
    app_id = st.number_input("Application id", min_value=1, step=1,
                             value=int(sample_ids[0]) if sample_ids else 1)
    if st.button("Score", type="primary"):
        try:
            r = httpx.post(f"{API}/v1/score", json={"application_id": int(app_id)}, timeout=10)
            r.raise_for_status()
            data = r.json()
            color = "🟢" if data["decision"] == "approve" else "🔴"
            st.success(f"{color} **{data['decision'].upper()}** — score {data['score']:.4f} "
                       f"(threshold {data['threshold']:.4f}, model v{data['model_version']})")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Scoring failed: {exc}")

# ------------------------------------------------------------------ docs
with tab_docs:
    st.markdown(
        """
        ## How this platform works
        1. **ETL** — seeded generator creates clients, applications (with 12-month
           default labels) and semi-structured JSONB transaction history; loaded
           via PostgreSQL `COPY`.
        2. **Features live in SQL** — `v_credit_features` (rolling windows,
           JSONB extraction, ratios). Training, serving and monitoring consume
           the same view: no train/serve skew by construction.
        3. **Training** — baseline vs RF vs XGBoost on a time-based split,
           isotonic calibration, cost-based threshold, everything logged to MLflow.
        4. **Serving** — FastAPI reads the Production model from the MLflow
           registry, scores applications, writes an audit log.
        5. **Monitoring** — Evidently drift/quality reports per production
           month; the simulation injects a downturn mid-2026 to show alerts firing.
        6. **Experiments** — A/B test sizing and uplift modelling for
           threshold/product changes.
        """
    )
