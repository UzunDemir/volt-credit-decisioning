"""Volt Credit Decisioning — end-to-end credit scoring platform.

Demo project for a Senior Data Scientist role (fintech).
Package layout:
  etl/        seeded data generation + PostgreSQL loading (structured + JSONB)
  model/      training pipeline, evaluation, cost-based thresholds, forecasting
  serving/    FastAPI production API
  monitoring/ Evidently drift / data-quality reports over simulated batches
  experiments/ uplift modelling + A/B test design utilities
  dashboard/  Streamlit business dashboard
"""

__version__ = "0.1.0"
