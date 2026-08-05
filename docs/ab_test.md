# A/B Test Design — new threshold rollout

Context: training finds a new cost-optimal threshold. Before switching
production traffic, validate that the business metrics move as expected —
and that we did not just overfit the threshold to validation noise.

## Hypothesis

- **H1 (primary)**: approving the marginal segment (score between old and
  new threshold) does not increase the bad rate among approved loans by more
  than 1 pp (12-month horizon).
- **H2 (guardrail)**: approval rate moves in the expected direction; average
  score of approved book does not collapse.
- **H3 (economics)**: expected margin per applicant does not decrease
  (expected_loss + opportunity_cost proxy).

## Design

- **Unit**: application (one unit = one decision).
- **Assignment**: random 10% holdout of traffic split into A (current
  threshold) / B (new threshold), 5% each; remainder continues on current
  policy. Randomized by `application_id % 100` (stable, no re-assignment).
- **Metrics**: approval rate (immediate), bad rate at 12m (delayed), score
  distribution (immediate proxy), cost per applicant (immediate proxy).
- **Sizing** (`credit_decision/experiments/ab_test.py`):

```python
from credit_decision.experiments.ab_test import sample_size_per_arm, days_needed
n = sample_size_per_arm(baseline_rate=0.10, mde=0.01, alpha=0.05, power=0.80)
days = days_needed(volume_per_day=5_000, n_per_arm=n, holdout_share=0.10)
```

For baseline bad rate 10% and MDE 1 pp: **~16k applications per arm**, which
at 5k applications/day with 10% holdout takes **~64 days**. If the decision
must be faster, either increase holdout share or accept a larger MDE.

## Guardrails & stopping rules

- Stop early if the *immediate proxy* (score distribution) shifts
  unexpectedly in B while A stays flat (likely assignment bug, not effect).
- Do not stop early on the delayed metric for significance chasing —
  pre-register the analysis: fixed horizon (12 months), two-sided test,
  alpha 0.05.
- If monitoring flags data drift *during* the experiment (downturn), the
  experiment result may be confounded — extend or re-run.

## Uplift alternative

Instead of a blanket threshold change, an **uplift model**
(`credit_decision/experiments/uplift.py`) can target the offer only to
clients whose marginal response is positive. The two-model demo shows the
top-20% uplift segment captures >30% of outcomes — i.e. targeting is
worthwhile and the blanket change can be limited to that segment.
