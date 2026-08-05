FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data/raw /app/data/processed /app/data/batches /app/mlartifacts \
    && mkdir -p /mlartifacts && chmod 1777 /mlartifacts  # Airflow tasks (uid 50000) write here

EXPOSE 8000 8501

CMD ["uvicorn", "credit_decision.serving.api:app", "--host", "0.0.0.0", "--port", "8000"]
