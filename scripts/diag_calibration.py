"""Diagnose calibration: why is test approval_rate 0%?"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from credit_decision.model import pipeline as pl
from credit_decision.model.evaluate import optimal_threshold
from credit_decision.model.train import time_split

df = pl.load_training_data()
train, val, test = time_split(df)
X_tr, y_tr = pl.split_features_target(train)
X_val, y_val = pl.split_features_target(val)
X_test, y_test = pl.split_features_target(test)
print(f"train={len(train):,} val={len(val):,} test={len(test):,}")

model = Pipeline([
    ("pre", pl.build_preprocessor()),
    ("clf", LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced")),
])
model.fit(X_tr, y_tr)

raw_val = model.predict_proba(X_val)[:, 1]
cal = CalibratedClassifierCV(estimator=FrozenEstimator(model), method="isotonic")
cal.fit(X_val, y_val)
cal_val = cal.predict_proba(X_val)[:, 1]
cal_test = cal.predict_proba(X_test)[:, 1]

t, cost = optimal_threshold(y_val.to_numpy(), cal_val, 1.0, 0.2)
print(f"threshold={t:.4f}  min_cost={cost:.0f}")
for name, s in [("raw_val", raw_val), ("cal_val", cal_val), ("cal_test", cal_test)]:
    print(f"{name:10s} min={s.min():.4f} p50={np.median(s):.4f} p90={np.percentile(s,90):.4f} "
          f"max={s.max():.4f} >thr={(s > t).mean():.3f}")

# raw scores on test for comparison
raw_test = model.predict_proba(X_test)[:, 1]
print(f"raw_test   min={raw_test.min():.4f} p50={np.median(raw_test):.4f} p90={np.percentile(raw_test,90):.4f} "
      f"max={raw_test.max():.4f} >thr={(raw_test > t).mean():.3f}")
