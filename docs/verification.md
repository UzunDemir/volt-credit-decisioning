# Verification guide — как проверить весь функционал платформы

Практический runbook: от «стек поднят» до champion-challenger и alert-петли.
Каждый пункт содержит команду и **ожидаемый результат** — если результат
другой, смотри раздел «Граббли и диагностика».

## 0. Предпосылки

- Docker Desktop запущен.
- Стек поднят (один раз):
  ```bash
  docker compose --profile full up -d
  ```
- Доступы: PostgreSQL `volt/volt` (хост-порт **5433**), Grafana `admin/volt`,
  Airflow `admin/volt`, MLflow без пароля.
- После пересоздания контейнеров Airflow — **жёсткий refresh браузера**
  (Ctrl+Shift+R) на http://localhost:8080.

## 1. Стек и контейнеры

```bash
docker compose ps -a
```

Ожидаемая картина:

| Контейнер | Статус |
|---|---|
| postgres | Up (healthy) |
| etl, train, monitor, airflow-init, grafana-alerts | Exited (0) — одноразовые |
| mlflow, api, dashboard | Up |
| airflow-scheduler, airflow-webserver | Up |
| grafana, prometheus | Up |

`Exited (0)` для одноразовых — норма, это не ошибка.

## 2. Данные и SQL-слой

```bash
docker compose exec postgres psql -U volt -d volt_credit -c   "SELECT (SELECT count(*) FROM clients) AS clients,           (SELECT count(*) FROM applications) AS applications,           (SELECT count(*) FROM transactions) AS transactions"
```
Ожидание: `150000 / 150000 / ~2.9M`.

Проверка feature-view (единый контракт для train/serve/monitor):
```bash
docker compose exec postgres psql -U volt -d volt_credit -c   "SELECT count(*) FROM v_credit_features WHERE has_default_12m IS NOT NULL"
```
Ожидание: ~150k строк. JSONB-слой:
```bash
docker compose exec postgres psql -U volt -d volt_credit -c   "SELECT details FROM transactions LIMIT 1"
```
Ожидание: JSON c `merchant/channel/mcc/hour/geo`, hour в 0..23.

## 3. Тренировка + MLflow

Полный прогон тренировки (заново обучает чемпиона, регистрирует в registry):
```bash
docker compose run --no-deps --rm train python -m credit_decision.model.train
```
Ожидание в логе: 5-fold CV AUC (logistic ~0.697), champion: logistic,
cost-optimal threshold ~0.157, TEST roc_auc ~0.69, `registered credit_scorer
vN -> alias 'production'`.

MLflow UI: http://localhost:5000 → эксперимент `credit_scoring` — run'ы с
параметрами/метриками/артефактами (`calibration_curve.csv`,
`feature_importance.csv`, `model_info.json`); Registry → `credit_scorer` —
алиасы `production`/`candidate`, теги `threshold`, `test_roc_auc`.

## 4. Serving API

Swagger: http://localhost:8000/docs

```bash
# здоровье и модель
curl http://localhost:8000/health                 # {"status":"ok"}
curl http://localhost:8000/model-info             # v11, threshold 0.157, test_roc_auc 0.6908

# скоринг по application_id (training-окно: 1..150000; production: 10000001+)
curl -X POST http://localhost:8000/v1/score -H "Content-Type: application/json" \
     -d '{"application_id": 100}'
# -> score (P(default)), decision approve/decline, threshold

# батч
curl -X POST http://localhost:8000/v1/score-batch -H "Content-Type: application/json" \
     -d '{"application_ids": [100, 101, 102]}'

# payload без БД (все 28 фич из v_credit_features)
curl -X POST http://localhost:8000/v1/score-payload -H "Content-Type: application/json" \
     -d '{"income": 3000, "amount": 6000, "term_months": 24, "age": 35,
          "employment_status": "employed", "purpose": "personal"}'

# аудит-лог
curl http://localhost:8000/v1/decisions/recent?limit=5
```

Каждый скоринг пишется в `decisions` (audit-лог дашборда).

**Degraded mode** (проверка graceful fallback): останови mlflow —
```bash
docker compose stop mlflow
curl -X POST http://localhost:8000/v1/score -H "Content-Type: application/json" -d '{"application_id": 100}'
# -> model_version: "fallback-rule", решение всё равно вернётся (НЕ 500)
docker compose start mlflow
```
API подхватит модель снова через TTL-кэш (≤60s).

## 5. Streamlit-дашборд

http://localhost:8501 — вкладки:
- **Business overview** — KPI (версия, threshold, AUC, scored, approval rate),
  заявки в день, распределение скоров;
- **Model** — детали модели + mix по версиям;
- **Monitoring** — drift по месяцам (01–03 чисто, 04+ красные) и средний скор;
- **Score demo** — живой скоринг из UI;
- **Docs** — как всё устроено.

Ожидание: KPI ~ «Model version 11, Threshold 0.157, ROC-AUC 0.6908».

## 6. Мониторинг и симуляция production

Полный цикл (7 месячных батчей 2026-01..07 + drift/quality отчёты):
```bash
docker compose run --no-deps --rm monitor python -m credit_decision.monitoring.run_monitor --simulate
```
Ожидание (~5-7 мин): батчи 1–3 `[steady]`, 5-й `[outage]`, 4/6/7
`[downturn]`; drift: `False` для 01–03, `True` для 04–07 (share 31–38%);
HTML-отчёты в `mlartifacts/monitoring/` (volumes), summary в
`monitoring_events`.

Проверка:
```bash
docker compose exec postgres psql -U volt -d volt_credit -c \
  "SELECT batch_name, (metrics->>'share_drifted')::float AS share, drift_detected \
   FROM monitoring_events WHERE report_type='data_drift' ORDER BY batch_name"
```
Ожидание: 2026-01..03 = 0.0/false; 04 = 0.31/true; 07 = 0.38/true.

## 7. Grafana и alert-петля

http://localhost:3000 (admin/volt) → дашборд **Volt ML Operations**:
- Drift share (stat) — красный ≥30%;
- Drift share by month — бары, 04+ красные;
- Approval rate, Applications per day, Latest decisions.

Alert: **Alerting → Rules** → «Drift share above 30%» — состояние **Firing**
(после прогона мониторинга, ~1 мин на оценку правила).

Проверка замкнутого цикла (rule → webhook → API → таблица):
```bash
docker compose exec postgres psql -U volt -d volt_credit -c \
  "SELECT title, state, received_at FROM alert_events ORDER BY event_id DESC LIMIT 5"
```
Ожидание: записи `[FIRING:1] Drift share above 30%`.

Ручная проверка webhook-цели:
```bash
curl -X POST http://localhost:8000/v1/alerts -H "Content-Type: application/json" \
     -d '{"title": "manual test", "state": "firing", "labels": {"severity": "warning"}}'
# -> {"received": true} и новая строка в alert_events
```

## 8. Airflow

http://localhost:8080 (admin/volt) → DAGs: 4 шт., все unpaused:

| DAG | Расписание | Что делает |
|---|---|---|
| daily_monitoring | 07:00 | Evidently-отчёт по последнему батчу (2026-07) |
| drift_retrain | 07:30 | drift ≥30% → кандидат (champion-challenger) |
| weekly_retrain | пн 03:00 | ретрейн + промоушен `production` |
| monthly_forecast | 1-е 04:00 | форкаст дефолт-рейта портфеля |

Проверка вручную:
- **Trigger DAG** (play в UI): daily_monitoring → Grid view → task `run_monitor`
  → лог: `drift_detected=True share=0.379...` (данные симуляции уже в БД).
- drift_retrain: лог `check_drift` → `latest drift share: 0.379 (threshold
  0.3)` → ShortCircuit пропустит к `retrain_candidate`.
- CLI из контейнера:
  ```bash
  docker compose exec airflow-scheduler airflow dags list
  ```

## 9. Champion-challenger (shadow scoring)

1. Зарегистрировать кандидата (production-алиас не трогается):
   ```bash
   docker compose run --no-deps --rm train python -m credit_decision.model.train --candidate
   ```
   Ожидание: `registered credit_scorer vN -> alias 'candidate' (NOT promoted)`.

2. **Без рестарта API** подождать ≤60s (TTL-кэш) и наскорить несколько заявок:
   ```bash
   curl -X POST http://localhost:8000/v1/score -H "Content-Type: application/json" \
        -d '{"application_id": 10000001}'
   ```
   (повторить для 10000002..10000005).

3. Проверка:
   ```bash
   docker compose exec postgres psql -U volt -d volt_credit -c \
     "SELECT count(*) FILTER (WHERE challenger_score IS NOT NULL) AS shadowed FROM decisions"
   ```
   Ожидание: >0. В Grafana панели «Shadow scoring» и «Recent shadow
   decisions» заполнятся (обновление ~30s).

## 10. Forecast

```bash
docker compose run --no-deps --rm train python -m credit_decision.model.forecast
```
Ожидание: серия месячных когорт 2023-01..2025-12, форкаст на 6 месяцев,
backtest MAPE; артефакты `forecast.png`/`forecast.csv` в MLflow
(эксперимент `portfolio_forecast`).

## 11. Эксперименты (без БД)

```bash
# из venv проекта
python -m credit_decision.experiments.uplift
python -m credit_decision.experiments.ab_test
```
Ожидание: uplift@20% > 30%, AUUC > 0.05; n per arm ~16k при MDE 1pp.

## 12. Тесты, линт, smoke

```bash
.venv\Scripts\python.exe -m pytest tests -q     # 27 passed, 3 skipped (нужна БД)
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe scripts/smoke_stack.py     # импорты + Evidently + API
.venv\Scripts\python.exe scripts/smoke_generate.py  # генератор + drift-сдвиги
.venv\Scripts\python.exe scripts/api_smoke.py       # /health /model-info /v1/score
```
CI (GitHub Actions): ruff + pytest + docker build + e2e (workflow_dispatch).

## 13. Чек-лист «всё зелёное» (эталонные значения)

- postgres healthy; etl/train/monitor Exited (0)
- /model-info: version 11, threshold 0.157, test_roc_auc 0.6908
- default rate training-окна ~9%; drift 0/0/0/31.0/31.0/34.5/37.9
- alert_events: свежие FIRING-записи
- decisions: есть строки с challenger_score IS NOT NULL
- Airflow: 4 DAG-а unpaused, manual trigger daily_monitoring → success
- git status пустой

## Граббли и диагностика

- **Повторный `up -d` перезапускает одноразовые сервисы** (etl/train/monitor
  заново, etl перезатрёт БД) — один раз поднял, дальше только `run --no-deps`.
- **`docker compose run` БЕЗ `--no-deps`** рекурсивно запускает зависимости —
  включая etl! Всегда указывай `--no-deps` для run-команд.
- **Airflow UI 400 на pause/unpause** → битая сессия после пересоздания
  webserver: жёсткий refresh, при необходимости перелогин.
- **Shadow-панели пусты** → нет кандидата (шаг 9.1) или не прошло 60s TTL.
- **Мониторинг «skips» месяцы** → production-батчи не загружены: запусти
  с `--simulate` (шаг 6).
- **Хост-порт postgres = 5433**, не 5432 (конфликт с локальным сервисом).
- **Всё висит / медленно** → проверь ресурсы Docker Desktop
  (Settings → Resources) и `docker system df` (диск).
