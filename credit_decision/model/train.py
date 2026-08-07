"""Training pipeline: baseline vs champion, honest CV, calibration, cost threshold.

Methodology (the parts that matter for a Senior DS interview):
  1. **Time-based split** — train < 2025-07, validation 2025-07..09,
     holdout test 2025-10..12. No random split: credit risk is non-stationary.
  2. **Honest estimate** — 5-fold stratified CV on the training window.
  3. **Calibration** — isotonic regression wrapped INTO the served model
     (CalibratedClassifierCV, fit on validation only): production scores are
     calibrated, so the cost-based threshold stays valid.
  4. **Threshold by business cost** — FP/FN cost ratio, tuned on validation,
     NOT on test (no threshold overfitting).
  5. **MLflow** — every run logged; champion registered & promoted via the
     `production` alias; threshold + metrics stored as model tags.

Usage:
    python -m credit_decision.model.train [--candidate]

    --candidate: train and REGISTER a candidate version, but do NOT promote
    the `production` alias (champion-challenger workflow: candidates are
    evaluated offline/shadow before promotion).
"""

from __future__ import annotations

import json
import time
from datetime import datetime

import mlflow
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier

from ..config import get_settings
from . import pipeline as pl
from .evaluate import (
    business_summary,
    calibration_points,
    classification_metrics,
    optimal_threshold,
)
from .pipeline import TARGET

TRAIN_END = "2025-06-30"
VAL_END = "2025-09-30"
MODEL_NAME = "credit_scorer"


def time_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = df[df["applied_at"] <= TRAIN_END]
    val = df[(df["applied_at"] > TRAIN_END) & (df["applied_at"] <= VAL_END)]
    test = df[df["applied_at"] > VAL_END]
    return train, val, test


def build_models() -> dict[str, Pipeline]:
    pre = pl.build_preprocessor()
    return {
        "logistic": Pipeline([
            ("pre", pre),
            ("clf", LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced")),
        ]),
        "random_forest": Pipeline([
            ("pre", pre),
            ("clf", RandomForestClassifier(
                n_estimators=300, max_depth=12, min_samples_leaf=50,
                class_weight="balanced", n_jobs=-1, random_state=42,
            )),
        ]),
        "xgboost": Pipeline([
            ("pre", pre),
            ("clf", XGBClassifier(
                n_estimators=500, max_depth=5, learning_rate=0.05,
                subsample=0.9, colsample_bytree=0.8, min_child_weight=5,
                scale_pos_weight=8, eval_metric="auc", n_jobs=4, random_state=42,
            )),
        ]),
    }


def main(candidate: bool = False) -> None:
    s = get_settings()
    t0 = time.time()

    mlflow.set_tracking_uri(s.mlflow_tracking_uri)
    mlflow.set_experiment("credit_scoring")

    print("Loading features from v_credit_features ...")
    df = pl.load_training_data()
    train, val, test = time_split(df)
    print(f"  train={len(train):,}  val={len(val):,}  test={len(test):,}  "
          f"(defaults: {train[TARGET].mean():.1%}/{val[TARGET].mean():.1%}/{test[TARGET].mean():.1%})")

    X_tr, y_tr = pl.split_features_target(train)
    X_val, y_val = pl.split_features_target(val)
    X_test, y_test = pl.split_features_target(test)

    run_name = f"{'candidate' if candidate else 'champion'}_{datetime.now():%Y%m%d_%H%M%S}"
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params({
            "n_train": len(train), "n_val": len(val), "n_test": len(test),
            "default_rate_train": train[TARGET].mean(),
            "cost_fp": s.model_cost_fp, "cost_fn": s.model_cost_fn,
        })

        # ---- 1. honest CV estimate -------------------------------------
        models = build_models()
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        results: dict[str, float] = {}
        for name, model in models.items():
            aucs = cross_val_score(model, X_tr, y_tr, cv=cv, scoring="roc_auc", n_jobs=1)
            results[name] = float(aucs.mean())
            mlflow.log_metric(f"cv_auc_mean_{name}", float(aucs.mean()))
            mlflow.log_metric(f"cv_auc_std_{name}", float(aucs.std()))
            print(f"  CV AUC {name:12s}: {aucs.mean():.4f} ± {aucs.std():.4f}")

        champion = max(results, key=results.get)
        mlflow.log_param("champion", champion)
        print(f"  champion: {champion}")

        # ---- 2. fit champion on train, calibrate on validation ---------
        model = models[champion]
        model.fit(X_tr, y_tr)
        val_score = model.predict_proba(X_val)[:, 1]
        mlflow.log_metric("val_auc_raw", roc_auc_score(y_val, val_score))

        # Calibration is part of the SERVED model, not a detached step:
        # FrozenEstimator marks the fitted pipeline as fixed; the wrapper
        # learns the isotonic map on validation only. Its predict_proba is
        # what production will use — threshold and scores stay consistent.
        calibrated = CalibratedClassifierCV(
            estimator=FrozenEstimator(model), method="isotonic"
        )
        calibrated.fit(X_val, y_val)
        val_cal = calibrated.predict_proba(X_val)[:, 1]

        # ---- 3. cost-optimal threshold on validation (not test!) -------
        threshold, min_cost = optimal_threshold(y_val, val_cal, s.model_cost_fp, s.model_cost_fn)
        mlflow.log_param("cost_optimal_threshold", threshold)
        mlflow.log_metric("val_min_total_cost", min_cost)
        print(f"  cost-optimal threshold (validation): {threshold:.3f}")

        # ---- 4. final metrics on holdout test --------------------------
        test_cal = calibrated.predict_proba(X_test)[:, 1]
        m = classification_metrics(y_test, test_cal)
        mlflow.log_metrics(m)
        biz = business_summary(
            y_test.to_numpy(), test_cal, threshold,
            test["amount"].to_numpy(), s.model_cost_fp, s.model_cost_fn,
        )
        mlflow.log_metrics({f"biz_{k}": v for k, v in biz.items() if isinstance(v, float)})
        print(f"  TEST roc_auc={m['roc_auc']:.4f}  gini={m['gini']:.4f}  ks={m['ks']:.3f}  "
              f"ece={m['ece']:.4f}  approval_rate={biz['approval_rate']:.1%}")

        # ---- 5. artifacts ----------------------------------------------
        calib = calibration_points(y_test.to_numpy(), test_cal)
        calib.to_csv("calibration_curve.csv", index=False)
        mlflow.log_artifact("calibration_curve.csv")
        with open("model_info.json", "w") as f:
            json.dump({"threshold": threshold, "metrics": m, "business": {k: v for k, v in biz.items() if isinstance(v, (int, float))}}, f, indent=2, default=str)
        mlflow.log_artifact("model_info.json")

        # permutation importance on a capped test sample (model-agnostic)
        imp = permutation_importance(
            model, X_test.head(10_000), y_test.head(10_000),
            n_repeats=3, scoring="roc_auc", random_state=42, n_jobs=-1,
        )
        imp_df = pd.DataFrame({
            "feature": pl.FEATURE_COLUMNS,
            "importance": imp.importances_mean,
        }).sort_values("importance", ascending=False)
        imp_df.to_csv("feature_importance.csv", index=False)
        mlflow.log_artifact("feature_importance.csv")

        # ---- 6. register -------------------------------------------------
        mlflow.sklearn.log_model(
            calibrated,  # preprocessor + estimator + isotonic calibration
            artifact_path="model",
            input_example=X_test.head(1),
            signature=mlflow.models.infer_signature(X_test.head(100), calibrated.predict(X_test.head(100))),
        )
        mv = mlflow.register_model(f"runs:/{run.info.run_id}/model", MODEL_NAME)
        client = mlflow.tracking.MlflowClient()
        client.set_model_version_tag(MODEL_NAME, mv.version, "threshold", str(threshold))
        client.set_model_version_tag(MODEL_NAME, mv.version, "test_roc_auc", f"{m['roc_auc']:.4f}")
        client.set_model_version_tag(MODEL_NAME, mv.version, "champion", champion)
        client.set_model_version_tag(MODEL_NAME, mv.version, "run_id", run.info.run_id)
        # aliases are the modern registry mechanism (stages were removed in MLflow 3.x)
        if candidate:
            client.set_registered_model_alias(MODEL_NAME, "candidate", mv.version)
            client.set_model_version_tag(MODEL_NAME, mv.version, "candidate", "true")
            print(f"  registered {MODEL_NAME} v{mv.version} -> alias 'candidate' (NOT promoted)")
        else:
            client.set_registered_model_alias(MODEL_NAME, "production", mv.version)
            print(f"  registered {MODEL_NAME} v{mv.version} -> alias 'production'")

    print(f"Training finished in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidate", action="store_true",
                    help="register as candidate (do not promote production alias)")
    args = ap.parse_args()
    main(candidate=args.candidate)
