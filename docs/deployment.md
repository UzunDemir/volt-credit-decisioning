# Deployment

Two paths: **Render** (free, no credit card — fastest way to get a public
link) and **Azure App Service** (matches the Azure nice-to-have on the
vacancy). Both run the same Docker image.

## 0. Local bootstrap (dev)

```bash
docker compose up --build
# etl and train run once, then exit; api + dashboard stay up:
#   API:       http://localhost:8000  (docs: /docs)
#   Dashboard: http://localhost:8501
#   MLflow:    http://localhost:5000

# simulate production months + drift reports:
docker compose run --rm monitor --simulate
```

## 1. Render (free)

1. Push the repo to GitHub.
2. Render → **New → Blueprint** (render.yaml reads `docker-compose.yml` is not
   supported; use *New Web Service* per service) — simpler: use
   **New Web Service** → connect repo → Dockerfile (root) → name `volt-api`:
   - Build: `Dockerfile`
   - Start command: `uvicorn credit_decision.serving.api:app --host 0.0.0.0 --port 8000`
   - Env vars: `POSTGRES_*`, `MLFLOW_TRACKING_URI`, `MODEL_APPROVAL_THRESHOLD`
   - Add a **PostgreSQL** add-on; point env vars at it.
3. The API must find a Production model. On Render there is no persistent
   MLflow — run the training job as a one-off:
   `docker compose run --rm train` locally, then copy the model artifacts
   (or point `MLFLOW_TRACKING_URI` at a hosted MLflow — e.g. a tiny Render
   service running the `ghcr.io/mlflow/mlflow` image with the same
   `--backend-store-uri postgresql://...`).
4. Add a second Web Service for the dashboard:
   `streamlit run credit_decision/dashboard/app.py --server.port 8501 --server.address 0.0.0.0`
   with `API_URL=https://volt-api.onrender.com`.

## 2. Azure App Service (matches the vacancy stack)

1. `az login`; create a resource group + PostgreSQL Flexible Server
   (or use the Azure Database for PostgreSQL in the same group).
2. Container:
   ```bash
   az acr create -n voltdemo -g volt-demo --sku Basic
   az acr build -r voltdemo -t volt-credit:latest .
   az webapp create -g volt-demo -p <plan> -n volt-credit-api \
     --deployment-container-image-name voltdemo.azurecr.io/volt-credit:latest
   ```
3. App settings (env vars): `POSTGRES_*`, `MLFLOW_TRACKING_URI`.
   For MLflow on Azure: run `mlflow server` on an App Service with
   PostgreSQL backend (same pattern as Render step 3), or use
   **Azure Machine Learning workspace registry** as `MLFLOW_TRACKING_URI`
   (azureml-mlflow plugin) — closest to the *Azure ML* nice-to-have.
4. Dashboard: second App Service, container start command
   `streamlit run ...`; set `API_URL` to the API host.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `POSTGRES_USER/PASSWORD/DB/HOST/PORT` | volt/volt/volt_credit/… | database |
| `MLFLOW_TRACKING_URI` | http://localhost:5000 | tracking + registry |
| `MODEL_APPROVAL_THRESHOLD` | 0.35 | fallback threshold (registry tag wins) |
| `DATA_SEED` | 42 | reproducibility |
| `DATA_N_APPLICATIONS` | 150000 | demo size (lower = faster bootstrap) |
| `MODEL_COST_FP / MODEL_COST_FN` | 1.0 / 0.2 | business cost ratio for threshold |

## Scaling notes (what to say in the interview)

- The API is stateless; horizontal scale behind a load balancer.
- Feature computation is per-application SQL — at higher throughput, move
  features to a nightly batch table and serve from there (same SQL).
- Spark/PySpark path: the ETL layer is the natural Spark migration for
  real volumes (`etl/` is DataFrame-based, portable to `pyspark.sql`).
- Monitoring runs on a schedule; alerts to Slack/PagerDuty via the
  `monitoring_events` rows.
