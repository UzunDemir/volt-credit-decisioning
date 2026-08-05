"""Seeded synthetic data generator for the credit decisioning demo.

Produces a realistic digital-lender dataset:
  * clients       — profile + hidden risk factor ``u ~ U[0,1]``
  * applications  — loan requests with a 12-month default label (train window)
  * transactions  — semi-structured JSONB payloads (merchant, channel, geo, hour)

Deterministic: the same seed always yields byte-identical data, which makes
the demo reproducible on any machine and enables drift simulation — production
batches are generated with shifted distributions (downturn scenario).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Taxonomies
# ---------------------------------------------------------------------------

CATEGORIES = [
    "groceries", "transport", "dining", "shopping", "utilities", "health",
    "entertainment", "cash", "travel", "education", "salary", "transfer",
]

MERCHANTS: dict[str, list[str]] = {
    "groceries": ["Carrefour", "Migros", "Tesco", "Lidl", "Auchan"],
    "transport": ["Uber", "Bolt", "Metro", "Railway", "GasStation"],
    "dining": ["McDonald's", "KFC", "LocalCafe", "PizzaHut", "SteakHouse"],
    "shopping": ["Amazon", "H&M", "Zara", "Decathlon", "AliExpress"],
    "utilities": ["ElectricCo", "WaterCo", "GasCo", "ISP"],
    "health": ["Pharmacy", "Clinic", "Dental", "Gym"],
    "entertainment": ["Netflix", "Spotify", "Cinema", "PlayStationStore"],
    "cash": ["ATM"],
    "travel": ["TurkishAirlines", "Booking", "Airbnb", "Expedia"],
    "education": ["Coursera", "University", "LanguageSchool"],
    "salary": ["Employer"],
    "transfer": ["P2P", "Remittance"],
}

# out-txn categories (salary / transfer are inflows)
_OUT_CATEGORIES = [c for c in CATEGORIES if c not in ("salary", "transfer")]
_OUT_WEIGHTS = np.array([0.24, 0.14, 0.11, 0.10, 0.08, 0.06, 0.05, 0.06, 0.03, 0.03])
_OUT_WEIGHTS = _OUT_WEIGHTS / _OUT_WEIGHTS.sum()
_IN_CATEGORIES = ["salary", "transfer"]

CITIES = ["Istanbul", "London", "Berlin", "Madrid", "Warsaw", "Bucharest", "Tbilisi", "Prague", "Vienna", "Kyiv"]
REGIONS = ["TR", "UK", "EU", "EE", "MEA"]
PURPOSES = ["personal", "debt_consolidation", "home_improvement", "education", "medical", "vehicle", "business"]
EMPLOYMENT = ["employed", "self_employed", "unemployed", "retired", "student"]
CHANNELS = ["card", "mobile", "online", "pos"]
TERMS = [6, 12, 24, 36, 48, 60]

# ---------------------------------------------------------------------------
# Scenario presets for production-batch drift simulation
# ---------------------------------------------------------------------------

DEFAULT_SCENARIOS: dict[str, dict[str, float]] = {
    # business as usual
    "steady": {},
    # economic downturn: incomes drop, unemployment rises, clients become
    # more cash-dependent, night & mobile activity increase, leverage grows
    "downturn": {
        "income_scale": 0.82,
        "p_unemp_add": 0.10,
        "night_add": 0.08,
        "mobile_add": 0.12,
        "loans_scale": 1.4,
        "cash_weight_scale": 1.8,
        "n_txns_scale": 0.9,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _categorical(rng: np.random.Generator, probs: np.ndarray) -> np.ndarray:
    """probs: (n, k) rows summing to 1 → integer category index per row."""
    u = rng.random(len(probs))
    return (u[:, None] > np.cumsum(probs, axis=1)).sum(axis=1)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

def _generate_core(
    rng: np.random.Generator,
    n: int,
    start: str,
    end: str,
    txn_min: int,
    txn_max: int,
    adj: dict[str, float] | None = None,
    id_offset: int = 0,
) -> dict[str, pd.DataFrame]:
    """Generate clients + applications + transactions for one time window."""
    adj = adj or {}
    income_scale = adj.get("income_scale", 1.0)
    p_unemp_add = adj.get("p_unemp_add", 0.0)
    night_add = adj.get("night_add", 0.0)
    mobile_add = adj.get("mobile_add", 0.0)
    loans_scale = adj.get("loans_scale", 1.0)
    cash_weight_scale = adj.get("cash_weight_scale", 1.0)
    n_txns_scale = adj.get("n_txns_scale", 1.0)

    # ---------------- clients -------------------------------------------
    u = rng.uniform(0.0, 1.0, n)  # hidden risk factor

    age = np.clip(rng.normal(38, 12, n), 18, 78).astype(int)
    income = 4000.0 * (1.2 - 0.45 * u) * np.exp(rng.normal(0.0, 0.35, n)) * income_scale

    p_unemp = np.clip(0.04 + 0.12 * u + p_unemp_add, 0.0, 0.4)
    p_self = 0.14 + 0.12 * u
    p_ret = np.full(n, 0.07)
    p_stu = np.clip(0.06 - 0.03 * u, 0.0, 0.1)
    p_emp = np.clip(1.0 - p_unemp - p_self - p_ret - p_stu, 0.0, 1.0)
    emp_idx = _categorical(rng, np.column_stack([p_emp, p_self, p_unemp, p_ret, p_stu]))
    employment = np.array(EMPLOYMENT)[emp_idx]

    # employment modulates income
    income = income * np.where(emp_idx == 2, 0.55, np.where(emp_idx == 4, 0.75, 1.0))
    income_missing = rng.random(n) < 0.07
    income = np.where(income_missing, np.nan, income)

    credit_history_months = np.clip(rng.poisson(55.0 * (1.3 - 0.6 * u), n), 0, 420).astype(int)
    num_open_loans = np.clip(rng.poisson((1.0 + 1.4 * u) * loans_scale, n), 0, 8).astype(int)

    region = rng.choice(REGIONS, n, p=[0.45, 0.20, 0.20, 0.10, 0.05])

    # ---------------- applications ---------------------------------------
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    app_offset_days = rng.integers(0, (end_ts - start_ts).days + 1, n)
    app_hour = rng.integers(0, 24, n)
    applied_at = start_ts + pd.to_timedelta(app_offset_days, unit="D") + pd.to_timedelta(app_hour, unit="h")

    gap_days = rng.integers(30, 901, n)
    first_seen_at = applied_at - pd.to_timedelta(gap_days, unit="D")
    segment = np.where(gap_days <= 90, "new", "existing")

    client_id = np.arange(1, n + 1) + id_offset
    clients = pd.DataFrame({
        "client_id": client_id,
        "first_seen_at": first_seen_at,
        "region": region,
        "segment": segment,
        "u": u,  # internal, dropped before load
    })

    monthly_income = np.where(income_missing, 5000.0, income)  # fallback for amount sizing
    amount = np.clip(monthly_income * np.exp(rng.normal(0.55, 0.5, n)), 500, 60000)
    amount = np.round(amount, 2)
    term_idx = rng.choice(len(TERMS), n, p=[0.08, 0.12, 0.20, 0.30, 0.18, 0.12])
    term_months = np.array(TERMS)[term_idx]

    p_personal = np.clip(0.42 - 0.10 * u, 0.05, 0.6)
    p_dc = np.clip(0.16 + 0.16 * u, 0.05, 0.5)
    p_home = np.full(n, 0.10)
    p_edu = np.full(n, 0.08)
    p_med = np.clip(0.06 + 0.04 * u, 0.0, 0.2)
    p_veh = np.full(n, 0.10)
    p_biz = np.clip(0.08 + 0.06 * u, 0.0, 0.25)
    p_pur = np.column_stack([p_personal, p_dc, p_home, p_edu, p_med, p_veh, p_biz])
    p_pur = p_pur / p_pur.sum(axis=1, keepdims=True)
    purpose = np.array(PURPOSES)[_categorical(rng, p_pur)]

    applications = pd.DataFrame({
        "application_id": np.arange(1, n + 1) + id_offset,
        "client_id": client_id,
        "applied_at": applied_at,
        "amount": amount,
        "term_months": term_months,
        "purpose": purpose,
        "income": np.where(income_missing, np.nan, np.round(income, 2)),
        "employment_status": employment,
        "age": age,
        "credit_history_months": credit_history_months,
        "num_open_loans": num_open_loans,
    })

    # ---------------- transactions --------------------------------------
    n_txns = np.clip(rng.lognormal(2.6, 0.9, n) * n_txns_scale, txn_min, txn_max).astype(int)
    total = int(n_txns.sum())
    client_idx = np.repeat(np.arange(n), n_txns)
    cu = u[client_idx]

    txn_id = np.arange(1, total + 1) + id_offset
    applied_s = applied_at.values.astype("datetime64[s]").astype("int64")
    offset_s = rng.integers(86400, 541 * 86400, total)          # 1..540 days before application
    txn_ts = pd.to_datetime(applied_s[client_idx] - offset_s, unit="s")

    night_p = np.clip(0.08 + 0.22 * cu + night_add, 0.0, 0.6)
    is_night = rng.random(total) < night_p
    hour = np.where(is_night, rng.integers(23, 24, total) + rng.choice([0, 0, 0, 1, 2, 3, 4, 5], total),
                    rng.integers(6, 23, total)).astype(int)

    p_in = np.clip(0.10 - 0.06 * cu, 0.01, 0.5)
    is_in = rng.random(total) < p_in

    out_scale = np.where(is_in, 1.0, (0.22 + 0.40 * cu))
    amount_scale = np.where(is_in,
                            monthly_income[client_idx] * np.exp(rng.normal(0.0, 0.25, total)),
                            (monthly_income[client_idx] / 30.0) * out_scale * np.exp(rng.normal(0.0, 0.45, total)))
    txn_amount = np.round(np.clip(amount_scale, 0.5, 200000.0), 2)

    # category: cash gets heavier in a downturn
    out_w = _OUT_WEIGHTS.copy()
    cash_i = _OUT_CATEGORIES.index("cash")
    out_w[cash_i] *= cash_weight_scale
    out_w = out_w / out_w.sum()
    cat_out = np.array(_OUT_CATEGORIES)[rng.choice(len(_OUT_CATEGORIES), total, p=out_w)]
    cat_in = np.array(_IN_CATEGORIES)[rng.choice(len(_IN_CATEGORIES), total, p=[0.6, 0.4])]
    category = np.where(is_in, cat_in, cat_out)

    # merchant per category
    merchant = np.empty(total, dtype=object)
    for cat in CATEGORIES:
        mask = category == cat
        if mask.any():
            pool = np.array(MERCHANTS[cat])
            merchant[mask] = pool[rng.integers(0, len(pool), int(mask.sum()))]

    # channel: mobile share grows with risk + downturn
    p_mobile = np.clip(0.16 + 0.34 * cu + mobile_add, 0.0, 0.8)
    r = rng.random(total)
    channel = np.where(r < 0.5, "card",
                       np.where(r < 0.5 + p_mobile, "mobile",
                                np.where(r < 0.65 + p_mobile, "online", "pos")))

    mcc = rng.integers(1000, 9000, total)
    city_primary = rng.integers(0, len(CITIES), n)[client_idx]
    city_txn = rng.integers(0, len(CITIES), total)
    city = np.where(rng.random(total) < 0.15, city_txn, city_primary)
    country = region[client_idx]

    details = [
        json.dumps(
            {"merchant": m, "channel": c, "mcc": int(mc), "hour": int(h),
             "geo": {"city": CITIES[ci], "country": co}},
            sort_keys=True, separators=(",", ":"),
        )
        for m, c, mc, h, ci, co in zip(merchant, channel, mcc, hour, city, country)  # noqa: B905 (py3.9 compat)
    ]

    transactions = pd.DataFrame({
        "txn_id": txn_id,
        "client_id": client_id[client_idx],
        "txn_ts": txn_ts,
        "amount": txn_amount,
        "direction": np.where(is_in, "in", "out"),
        "category": category,
        "details": details,
        # internal helpers (dropped before load)
        "_is_night": is_night,
        "_is_in": is_in,
        "_client_idx": client_idx,
    })

    return {"clients": clients, "applications": applications, "transactions": transactions}


def compute_labels(
    clients: pd.DataFrame,
    applications: pd.DataFrame,
    transactions: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Attach the 12-month default label.

    The label depends on the hidden risk factor ``u`` AND on observable
    transaction behaviour (utilization, night spend share) — exactly the
    signal the feature pipeline extracts. No target leakage: only
    pre-application transactions are used.
    """
    tx = transactions
    # per-client offsets: recompute from client's own applied_at
    app_by_client = applications.set_index("client_id")["applied_at"]
    tx_days = (app_by_client.reindex(tx["_client_idx"].values).values - tx["txn_ts"].values)
    tx_days = tx_days.astype("timedelta64[D]").astype(int)

    out30 = tx["_is_in"].eq(False) & (tx_days <= 30)
    out90 = tx["_is_in"].eq(False) & (tx_days <= 90)

    g = tx.assign(_out30=out30, _out90=out90, _night=tx["_is_night"] & out90)
    sums30 = g.loc[g["_out30"], ["client_id", "amount"]].groupby("client_id")["amount"].sum()
    cnt90 = g.loc[g["_out90"], ["client_id", "amount"]].groupby("client_id")["amount"].count()
    night90 = g.loc[g["_night"], ["client_id", "amount"]].groupby("client_id")["amount"].count()

    meta = clients.set_index("client_id")
    income = applications.set_index("client_id")["income"]
    util_30 = (sums30.reindex(meta.index) / income.reindex(meta.index)).fillna(0.0)
    night_share = (night90.reindex(meta.index) / cnt90.reindex(meta.index)).fillna(0.0)

    logit = -3.8 + 3.0 * meta["u"]
    logit = logit + 0.9 * (util_30 - 0.45) + 0.8 * (night_share - 0.12)

    default = pd.Series(rng.random(len(meta)) < _sigmoid(logit.to_numpy()), index=meta.index)
    applications = applications.copy()
    applications["has_default_12m"] = default.loc[applications["client_id"]].to_numpy()
    return applications


def generate_dataset(
    seed: int = 42,
    n_applications: int = 150_000,
    txn_min: int = 3,
    txn_max: int = 220,
    start: str = "2023-01-01",
    end: str = "2025-12-31",
) -> dict[str, pd.DataFrame]:
    """Full training window: clients + labeled applications + transactions."""
    rng = _rng(seed)
    out = _generate_core(rng, n_applications, start, end, txn_min, txn_max)
    out["applications"] = compute_labels(out["clients"], out["applications"], out["transactions"], rng)
    return out


def generate_production_batch(
    seed: int = 42,
    month_index: int = 0,
    scenario: str = "steady",
    n: int = 5_000,
    txn_min: int = 3,
    txn_max: int = 220,
    id_offset: int = 0,
) -> dict[str, pd.DataFrame]:
    """One unlabeled production month (2026-01 + month_index).

    ``scenario`` shifts the data-generating distribution to simulate drift:
    month 0-2 "steady", month 3+ "downturn".
    """
    rng = _rng(seed + month_index * 7919)
    start = f"2026-{month_index + 1:02d}-01"
    end = f"2026-{month_index + 1:02d}-28"
    adj = DEFAULT_SCENARIOS.get(scenario, {})
    out = _generate_core(rng, n, start, end, txn_min, txn_max, adj, id_offset)
    apps = out["applications"].copy()
    apps["has_default_12m"] = None
    out["applications"] = apps
    out["scenario"] = scenario
    return out


def drop_internal_columns(dfs: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Strip generator-internal columns before loading into PostgreSQL."""
    return {
        "clients": dfs["clients"].drop(columns=["u"]),
        "applications": dfs["applications"],
        "transactions": dfs["transactions"].drop(columns=["_is_night", "_is_in", "_client_idx"]),
    }
