"""MLflow 3.x skops serialization guard — regression test.

mlflow 3.x refuses to save sklearn models that reference "untrusted" types
(e.g. numpy.dtype) unless explicitly whitelisted via ``skops_trusted_types``.
This test reproduces the real pipeline shape (preprocessor + XGBoost) and
verifies log -> load -> predict roundtrip.
"""

from __future__ import annotations

import tempfile

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier

from credit_decision.model.pipeline import (
    FEATURE_COLUMNS_CATEGORICAL,
    FEATURE_COLUMNS_NUMERIC,
    build_preprocessor,
)


def test_skops_roundtrip_with_trusted_types():
    rng = np.random.default_rng(0)
    n = 300
    X = pd.DataFrame(
        {col: rng.uniform(0, 1000, n) for col in FEATURE_COLUMNS_NUMERIC}
    )
    X["employment_status"] = rng.choice(["employed", "self_employed", "unemployed"], n)
    X["purpose"] = rng.choice(["personal", "vehicle", "business"], n)
    assert set(FEATURE_COLUMNS_CATEGORICAL) <= set(X.columns)
    y = (X["income"] > X["income"].median()).astype(int)

    model = Pipeline([
        ("pre", build_preprocessor()),
        ("clf", XGBClassifier(n_estimators=20, max_depth=3, eval_metric="auc")),
    ])
    model.fit(X, y)

    # the artifact shape shipped to production: estimator + isotonic calibration
    calibrated = CalibratedClassifierCV(estimator=FrozenEstimator(model), method="isotonic")
    calibrated.fit(X, y)

    import mlflow

    # sqlite file stays locked by the mlflow client on Windows until GC,
    # so cleanup errors are expected and ignored
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        mlflow.set_tracking_uri(f"sqlite:///{tmp}/mlflow.db")
        with mlflow.start_run():
            mlflow.sklearn.log_model(
                calibrated,
                artifact_path="model",  # classic 2.x flow: artifact lands in the run
                input_example=X.head(1),
            )
            loaded = mlflow.sklearn.load_model(f"runs:/{mlflow.active_run().info.run_id}/model")
        preds = loaded.predict_proba(X)
        assert preds.shape == (n, 2)
        assert np.isfinite(preds).all()
