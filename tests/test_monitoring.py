"""Unit tests for the monitoring job — no database required."""

from __future__ import annotations

import pandas as pd

from credit_decision.model.pipeline import FEATURE_COLUMNS
from credit_decision.monitoring.run_monitor import _score


class _DummyModel:
    def predict_proba(self, X):
        raise AssertionError("predict must not be called on empty input")


def test_score_empty_frame_returns_empty():
    empty = pd.DataFrame(columns=FEATURE_COLUMNS)
    out = _score(empty, _DummyModel())
    assert out.empty
