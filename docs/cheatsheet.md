# Cheat sheet — все сервисы Volt Credit Decisioning

Быстрый справочник: роль, доступ, команды, ожидания, грабли. Команды
проверены на живом стеке (2026-08, модель v11 / кандидат v12).

## Общее

| Что | Значение |
|---|---|
| Порт postgres на хосте | **5433** (в контейнерах — postgres:5432) |
| API / Dashboard / MLflow | 8000 / 8501 / 5000 |
| Grafana / Prometheus / Airflow | 3000 / 9090 / 8080 |
| Креды | postgres `volt/volt`; grafana, airflow `admin/volt` |
| Профили | base: `docker compose up -d`; full: `docker compose --profile full up -d` |
| Одноразовые (Exited 0 = норма) | etl, train, monitor, airflow-init, grafana-alerts |
| Тома | `pgdata`, `mlartifacts` (общий: api/train/monitor/airflow-scheduler), `grafana-storage` |
| Модель | v11 `production` (logistic, threshold 0.157, AUC 0.6908, approval 86.2%), v12 `candidate` |

**Три главных грабля:**
1. `docker compose run` **без `--no-deps`** рекурсивно запускает одноразовые зависимости (etl!). Всегда `--no-deps`.
2. Повторный `up -d` заново запускает etl/train/monitor (etl перезатрёт БД).
3. После пересоздания контейнеров Airflow — жёсткий refresh браузера (Ctrl+Shift+R).

---

## postgres

- **Роль**: единственное хранилище. Таблицы: `clients`, `applications`,
  `transactions` (JSONB `details`), `decisions` (аудит-лог API),
  `monitoring_events` (дрифт/качество), `alert_events` (вебхук-алерты).
  Отдельная БД `volt_airflow` — метастор Airflow. View `v_credit_features` —
  единый feature-контракт (train/serve/monitor).
- **Вход**: `docker compose exec postgres psql -U volt -d volt_credit`
- **Полезные запросы**:
  ```sql
  SELECT (SELECT count(*) FROM clients), (SELECT count(*) FROM applications),
         (SELECT count(*) FROM transactions);
  SELECT batch_name, (metrics->>'share_drifted')::float AS share, drift_detected
  FROM monitoring_events WHERE report_type='data_drift' ORDER BY batch_name;
  SELECT count(*) FILTER (WHERE challenger_score IS NOT NULL) FROM decisions;
  SELECT title, state, received_at FROM alert_events ORDER BY event_id DESC LIMIT 5;
  ```
- **Грабли**: etl делает `TRUNCATE ... decisions` — аудит-лог стирается при
  каждом прогоне ETL; init-скрипты `sql/*.sql` выполняются только при первом
  подъёме пустого тома (старые тома догоняет runtime-гард в api).

## mlflow

- **Роль**: tracking + registry.
- **UI**: http://localhost:5000. Эксперименты: `credit_scoring` (train),
  `portfolio_forecast` (forecast), Default (мониторинг).
- **Registry**: модель `credit_scorer`, алиасы `production` / `candidate`,
  теги: `threshold`, `test_roc_auc`, `champion`, `run_id`, `candidate=true`.
- **Артефакты**: том `mlartifacts` (у контейнеров — `/mlartifacts`).
- **Проверка**: `docker compose exec api python scripts/mlflow_check.py` (нужен
  MLFLOW_TRACKING_URI) или UI.
- **Грабли**: mlflow 2.x (пин `<3`); при `docker compose stop mlflow` API
  переходит в fallback-rule, после `start mlflow` модель возвращается через
  TTL (≤60s).

## etl

- **Роль**: генерация seed-данных (детерминированная) + COPY в PostgreSQL.
- **Запуск**: `docker compose up -d` (сам) или
  `docker compose run --no-deps --rm etl python -m credit_decision.etl.run_etl`
- **Env** (в `.env`): `DATA_SEED=42`, `DATA_N_APPLICATIONS=150000`,
  `DATA_N_TXNS_PER_CLIENT_MIN/MAX=3/220`.
- **Ожидание**: ~4–8 мин (на этой машине 238s); 150k/150k/~2.9M; default rate
  ~9%; `Exited (0)`.
- **Грабли**: TRUNCATE clients/applications/transactions/decisions; повторный
  `up` перезапускает; run без `--no-deps` тянет за собой postgres+зависимости.

## train

- **Роль**: обучение (time-split, 5-fold CV, isotonic-калибровка, cost-порог)
  + регистрация в MLflow.
- **Команды**:
  ```bash
  # чемпион (алиас production):
  docker compose run --no-deps --rm train python -m credit_decision.model.train
  # кандидат (production не трогаем):
  docker compose run --no-deps --rm train python -m credit_decision.model.train --candidate
  # форкаст портфеля:
  docker compose run --no-deps --rm train python -m credit_decision.model.forecast
  ```
- **Ожидание**: ~90s; CV AUC logistic ~0.697 > rf 0.694 > xgb 0.690;
  threshold 0.157; TEST roc_auc ~0.69; `registered credit_scorer vN ->
  alias 'production'` (или `'candidate' (NOT promoted)`).
- **Грабли**: sklearn ≥1.6 (FrozenEstimator); кандидат-логистика на тех же
  данных даёт скоры, идентичные чемпиону (shadow diff = 0 — это норма).

## api

- **Роль**: serving + аудит + вебхук-цель + метрики.
- **Swagger**: http://localhost:8000/docs
- **Эндпоинты**: `/health`, `/model-info`, `/v1/score`,
  `/v1/score-batch`, `/v1/score-payload` (без БД, все 28 фич),
  `/v1/decisions/recent`, `/v1/alerts` (Grafana webhook),
  `/metrics` (Prometheus).
- **Внутренности**: TTL-кэш модели 60s (ретрайн подхватывается без рестарта);
  schema-guard при старте (`challenger_score`, `alert_events`);
  degraded mode: mlflow недоступен → `model_version: "fallback-rule"`,
  но решение возвращается (не 500).
- **Смоук**: `python scripts/api_smoke.py` (из venv на хосте).
- **Грабли**: рестарт не нужен после ретрейна; скоринг пишет в `decisions`.

## dashboard

- **Роль**: Streamlit для бизнеса.
- **UI**: http://localhost:8501. Вкладки: Business overview (KPI, заявки в
  день, распределение скоров), Model (версия, метрики, mix), Monitoring
  (дрифт по месяцам), Score demo (живой скоринг), Docs.
- **Кэш**: `st.cache_data` ttl 30–60s (данные обновляются с задержкой).
- **Ожидание**: KPI v11 / 0.157 / 0.6908; бары дрифта: 01–03 чисто, 04+ красные.
- **Грабли**: требует БД + API; `streamlit_app.py` в корне — standalone-демо
  без БД (захардкоженные числа, для Streamlit Cloud).

## monitor

- **Роль**: симуляция production-батчей + Evidently drift/quality.
- **Команды**:
  ```bash
  # полный цикл (7 батчей + отчёты):
  docker compose run --no-deps --rm monitor python -m credit_decision.monitoring.run_monitor --simulate
  # один месяц (как daily_monitoring DAG):
  docker compose run --no-deps --rm monitor python -m credit_decision.monitoring.run_monitor --month 2026-07
  ```
- **Ожидание**: ~5–7 мин; батчи steady (01–03), outage (05), downturn
  (04, 06–07); drift share 0/0/0/31.0/31.0/34.5/37.9; отчёты
  `mlartifacts/monitoring/drift_*.html`, `quality_*.html`.
- **Грабли**: без `--simulate` на свежей БД месяцы пустые → «no
  applications — skipping» (это штатно, не ошибка — раньше падало, починено);
  окно месяца жёстко до 28-го числа (детерминизм).

## prometheus

- **Роль**: API SLO-слой (RPS, латентность, ошибки).
- **UI**: http://localhost:9090 → Graph / Targets. Таргет `api` должен быть
  **up** (скрейп 15s, `prometheus/prometheus.yml`).
- **Метрики**: `http_requests_total{handler,method,status}` (status — класс
  2xx/4xx/5xx), `http_request_duration_seconds{handler}`,
  `http_request_duration_highr_seconds` (точные квантили, без handler),
  `http_request_size_bytes`, `http_response_size_bytes`, `process_*`.
- **PromQL**:
  ```promql
  sum(rate(http_requests_total[5m]))                                        # RPS
  sum by (handler) (rate(http_requests_total[5m]))                          # по эндпоинтам
  sum(rate(http_requests_total{status="5xx"}[5m]))/sum(rate(http_requests_total[5m]))  # error rate
  histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))  # p95
  ```
- **Генерация трафика** (PowerShell):
  ```powershell
  for ($i=1; $i -le 20; $i++) { $b = '{"application_id": ' + (10000000+$i) + '}';
    curl.exe -s -X POST http://localhost:8000/v1/score -H "Content-Type: application/json" -d $b | Out-Null }
  ```
- **Грабли**: статусы сгруппированы по классам; без трафика графики плоские.

## grafana

- **Роль**: дашборды + алертинг.
- **UI**: http://localhost:3000 (admin/volt).
- **Datasources** (provisioned): `VoltPostgres` (uid `voltdb`, default) —
  бизнес-метрики; `VoltPrometheus` (uid `voltprom`) — SLO.
- **Дашборд**: «Volt ML Operations» (`grafana/dashboards/volt_ml.json`,
  file provider, refresh 30s): drift share, drift по месяцам, approval rate,
  заявки в день, последние решения, shadow-панели.
- **Алертинг**: правило «Drift share above 30%» (провижинится скриптом);
  политика: `severity=warning` → контакт `volt-webhook` →
  `POST api:8000/v1/alerts` → `alert_events`. Оценка правила — раз в минуту.
- **Проверка**: Alerting → Rules → Firing; `SELECT ... FROM alert_events`.
- **Грабли**: правило создаётся API-скриптом, а не файлом provisioning
  (folder-race); ручной тест webhook:
  `curl -X POST localhost:8000/v1/alerts -H "Content-Type: application/json" -d '{"title":"t","state":"firing"}'`.

## grafana-alerts

- **Роль**: одноразовый провижининг: папка `Volt`, правило, контакт-поинт,
  политика (idempotent — повторный запуск ничего не дублирует).
- **Env**: `GRAFANA_URL`, `GRAFANA_ADMIN_USER/PASSWORD`, `ALERT_WEBHOOK_URL`.
- **Ожидание**: `Exited (0)` через ~1 мин после старта Grafana.
- **Грабли**: если Grafana не готова за 120s — exit с ошибкой (композ
  перезапустит при следующем up); root-ресивер политики тоже указывает на
  webhook (все алерты, не только warning).

## airflow-init

- **Роль**: создание БД `volt_airflow`, `airflow db migrate`, админ
  (admin/volt).
- **Ожидание**: `Exited (0)`; scheduler/webserver стартуют только после него.
- **Грабли**: повторный запуск идемпотентен; на медленном хосте может идти
  несколько минут.

## airflow-scheduler

- **Роль**: планировщик; LocalExecutor — задачи DAG'ов выполняются **внутри
  этого контейнера** (поэтому смонтирован `mlartifacts`).
- **DAG-и** (код из образа, `/app/dags`):

  | DAG | Расписание | Действие |
  |---|---|---|
  | daily_monitoring | 07:00 | Evidently по последнему батчу (2026-07) |
  | drift_retrain | 07:30 | drift ≥30% → `train --candidate` |
  | weekly_retrain | пн 03:00 | ретрейн + промоушен `production` |
  | monthly_forecast | 1-е 04:00 | форкаст дефолт-рейта |

- **CLI**:
  ```bash
  docker compose exec airflow-scheduler airflow dags list
  docker compose exec airflow-scheduler airflow dags trigger daily_monitoring
  ```
- **Грабли**: DAG-код запечён в образ — после правок `dags/` нужна пересборка
  `volt-airflow` и пересоздание scheduler/webserver; таски ходят в БД как
  uid 50000 (том mlartifacts с правами 1777).

## airflow-webserver

- **Роль**: UI + REST API.
- **UI**: http://localhost:8080 (admin/volt).
- **REST** (basic auth):
  ```bash
  # список DAG-ов
  curl -u admin:volt http://localhost:8080/api/v1/dags
  # unpause
  curl -u admin:volt -X PATCH http://localhost:8080/api/v1/dags/drift_retrain \
       -H "Content-Type: application/json" -d '{"is_paused": false}'
  ```
- **Грабли**: UI-POST'ы (pause/unpause) требуют CSRF-токен сессии — после
  пересоздания контейнера старые вкладки ловят 400 («CSRF session token is
  missing») → жёсткий refresh. Ключи `AIRFLOW__WEBSERVER__SECRET_KEY` и
  `AIRFLOW__CORE__FERNET_KEY` закреплены в compose (переопределяются через
  `AIRFLOW_SECRET_KEY` / `AIRFLOW_FERNET_KEY`).

## PromQL-алерты (Grafana)

Механика та же, что у drift-правила: правило → label `severity=warning` →
политика → контакт `volt-webhook` → `POST api:8000/v1/alerts` →
таблица `alert_events`. Datasource — `VoltPrometheus` (uid `voltprom`).

Структура правила (как в `scripts/grafana_alerts.py`): refId **A** — PromQL
запрос, **B** — reduce (`last` за интервал), **C** — threshold
(`gt`/`lt`/`is_below`), плюс `for` (сколько времени условие должно
держаться до Firing).

### Готовые правила

```promql
# 1) Error rate > 1% за 5 мин (5xx от общего трафика)
A: (sum(rate(http_requests_total{status="5xx"}[5m])) or vector(0)) / sum(rate(http_requests_total[5m]))
B: reduce last, C: gt 0.01, for: 5m

# 2) p95 задержки > 300ms за 5 мин
A: histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))
B: reduce last, C: gt 0.3, for: 5m

# 3) Скоринг молчит (RPS по /v1/score ~ 0) 15 минут — тихий отказ
A: sum(rate(http_requests_total{handler="/v1/score"}[10m]))
B: reduce last, C: lt 0.01, for: 15m

# 4) API недоступен для скрейпа
A: up{job="api"}
B: reduce last, C: is_below 0.5, for: 2m
```

Нюансы:
- `0/0 = NaN` (нет трафика): правило уйдёт в **NoData**, а не Firing — для
  error rate это правильно. Хочешь «молчание = алерт» — добавь второе
  условие на минимальный трафик:
  `A: sum(rate(http_requests_total[5m])) > 0.1` (например, как C2 с
  оператором AND).
- `status` — это класс (`5xx`), не код; `handler` — роут (`/v1/score`).
- Гистограмма: квантиль считается по `_bucket` + `le`.

### Как добавить

Вариант A — **кодом** (уже сделано, идемпотентно):
правила «Error rate above 1% (5xx)» и «API p95 latency above 300ms»
провижинятся `scripts/grafana_alerts.py` (SLO_RULES) вместе с drift-правилом
— папка `Volt`, контакт-поинт и политика уже на месте.

Вариант B — **UI**: Alerting → Alert rules → New rule: datasource
VoltPrometheus → PromQL → Reduce (last) → Threshold → `for` → папка Volt →
labels `severity=warning` → сохранить. Доставка — автоматически через
политику.

### Проверка

```bash
# после срабатывания — запись в БД:
docker compose exec postgres psql -U volt -d volt_credit -c   "SELECT title, state, received_at FROM alert_events ORDER BY event_id DESC LIMIT 5"
# форсировать: временно снизить порог в правиле или нагенерить ошибки:
curl -s -X POST http://localhost:8000/v1/score-batch -H "Content-Type: application/json"      -d '{"application_ids": [-1, -2, -3]}'   # 404/422 -> 4xx, error rate не тронет (только 5xx)
```

---

## Git-процесс

История: 1 корневой коммит + 6 рабочих (milestone, фиксы, доки) — всё на
`main`, дерево чистое, публиковать можно сразу.

### Стандартный цикл

```bash
git status                 # что изменилось (M/??/D)
git diff                   # посмотреть правки (--stat — сводка)
git diff --cached          # что уже в индексе

# до коммита — обязательно:
.venv\Scripts\python.exe -m pytest tests -q     # 27 passed / 3 skipped
.venv\Scripts\python.exe -m ruff check .        # чисто

git add -A
git commit -m "Короткий заголовок (что сделано)

Тело: зачем, что менялось, как проверено (1-5 строк)."
git push origin main
```

Конвенция коммитов в этом репо: заголовок — глагол + суть
(«Fix monitoring on empty production months»), тело — контекст и
результаты проверки (E2E-цифры и т.п.).

### Что НЕ коммитится (.gitignore)

`.env` (креды), `.venv/`, `mlruns/`, `mlartifacts/`, `data/raw|processed|batches`,
`notebooks/`, `scripts/playground/`, `*_out.txt` (логи команд), личные
`cv.txt / project.txt / vacantion.txt`. Проверка:
`git status --short --ignored | head`.

### CI (после push)

`.github/workflows/ci.yml`:
1. **lint** — ruff по всему репо;
2. **unit-tests** — pytest (DB-интеграционные скипаются без POSTGRES_HOST);
3. **docker-build** — сборка образа из корневого Dockerfile;
4. **e2e** — вручную (`workflow_dispatch`): полный bootstrap
   `docker compose up --build` + smoke API.

### После изменения кода — что пересобрать

| Что поменял | Действие |
|---|---|
| `credit_decision/**` (код) | `docker compose build <сервис>` + `up -d --force-recreate <сервис>` |
| `dags/` | `docker compose --profile full build airflow-init` + recreate scheduler/webserver |
| `docker-compose.yml` | `up -d --force-recreate` (изменился конфиг) |
| `docs/`, `tests/` | ничего не пересобирать |

### Откат / история

```bash
git log --oneline -10        # история
git show <sha> --stat        # что в коммите
git revert <sha>             # безопасный откат (новый коммит)
git reset --hard <sha>       # жёсткий откат (осторожно: удалит правки)
```

### Как работать над фичей

```bash
git checkout -b feature/slo-alerts
# ...правки, тесты, ruff...
git commit -m "..."
git checkout main && git merge feature/slo-alerts
```

Релиз: когда захочешь зафиксировать состояние — `git tag v0.1.0 && git push --tags`.

---

## Полезные файлы

- `docker-compose.yml` — вся инфраструктура (сервисы, профили, тома)
- `.env` / `.env.example` — настройки данных и модели (gitignored)
- `sql/01_schema.sql`, `sql/02_features.sql` — схема + feature-view (источник истины)
- `dags/` — 4 DAG-а Airflow
- `grafana/`, `prometheus/` — provisioning
- `scripts/` — smoke (`api_smoke`, `smoke_stack`, `smoke_generate`),
  провижининг (`airflow_init`, `grafana_alerts`), диагностика (`mlflow_check`)
- `docs/verification.md` — пошаговый гайд проверки всего функционала
- `docs/demo_script.md` — сценарий для собеседования
