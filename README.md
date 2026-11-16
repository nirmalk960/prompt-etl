# Prompt ETL Studio

An open, self-hosted visual ETL platform. Build data pipelines on a
drag-and-drop canvas from **37 ready-made operations** — filters, joins,
pivots, window functions, data quality checks, **predictions** (regression,
classification, clustering, anomalies, forecasting) and **dashboards** — and
fill in each box's settings like a traditional ETL tool, with no AI required.
When a step is easier to describe than to configure, switch the box to
**Prompt** mode (or use a **Custom** box) and a local LLM writes the code.
Live progress streams back, backed by object storage, an async job queue, and
a tiered compute router that picks the right engine for the data size.
The project is released under the [MIT license](LICENSE).

Prompt ETL auto-picks the right compute engine for the data size:

| Engine    | Sweet spot                                    | When the router picks it                          |
| --------- | --------------------------------------------- | ------------------------------------------------- |
| pandas    | < 1 GB, rich Python ecosystem                 | small datasets, fits in `RAM/4`                   |
| Polars    | 1 - 20 GB, lazy / streaming, multi-core       | medium datasets, fits in `RAM/2`                  |
| DuckDB    | 1 - 50+ GB, out-of-core SQL                   | SQL ops, or dataset > `RAM/2` (spills to disk)    |
| Spark     | > 50 GB, distributed                          | Phase 2: stubbed; submits SparkApplication on K8s |

The router reads cgroup v2 memory limits at startup, so the **same container
image** behaves correctly on a 16 GB laptop and a 256 GB worker node.

---

## Table of contents

- [Architecture](#architecture)
- [Operations](#operations)
- [Dashboards](#dashboards)
- [Demo boards](#demo-boards)
- [Two ways to run it](#two-ways-to-run-it)
- [Build from scratch (development)](#build-from-scratch-development)
- [Single-image deployment](#single-image-deployment)
- [LLM setup (Ollama)](#llm-setup-ollama)
- [Repo layout](#repo-layout)
- [Configuration](#configuration)
- [Testing](#testing)
- [Publishing](#publishing)
- [What's implemented](#whats-implemented-phase-1)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)

---

## Architecture

```
              ┌──────────────┐
              │  Web UI      │   React + Vite + Tailwind (Prompt ETL Studio canvas)
              └──────┬───────┘
                     │ REST + SSE
              ┌──────▼───────┐
              │ FastAPI      │   presigned URLs, job enqueue, SSE fan-out
              │  gateway     │
              └──┬─────┬─────┘
       enqueue  │     │  pubsub
                │     │
          ┌─────▼──┐  │      ┌──────────────┐
          │ Redis  ◄──┘      │ Postgres     │   datasets, workflows, jobs
          └──┬─────┘         └──────────────┘
             │ pull
       ┌─────▼─────────┐
       │ Arq worker(s) │   runs EngineRouter -> pandas / Polars / DuckDB
       └──┬────────────┘
          │ read/write Parquet
       ┌──▼──────────┐
       │ S3 / MinIO  │   raw uploads + parquet datasets + step checkpoints
       │ (or local)  │
       └─────────────┘
```

The **control plane** (API + Postgres + Redis) is separate from the **data
plane** (workers + object store), so each scales independently. Phase 2 adds a
`SparkEngine` adapter that submits a `SparkApplication` CR to the Spark
Kubernetes Operator. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Operations

Every box on the canvas is an operation from the catalog served by
`GET /api/operations`; the palette, settings forms and preflight checks are all
generated from it. Each box has two modes:

- **Configure** (default): pick columns and options in the Inspector. SQL
  operations compile to a single DuckDB query that runs in a sandbox (no file,
  network or environment access beyond the step's own input and output).
  Python operations run scikit-learn / statsmodels / Plotly.
- **Prompt (AI)**: describe the step in plain English; the local LLM writes
  pandas / Polars / DuckDB code for it. Prediction and dashboard boxes are
  configure-only; the **Custom** box is prompt-only.

While you edit, the canvas asks the server for each box's output columns
(`POST /api/pipelines/schema`, a dry run on empty tables), so column pickers
stay up to date and mistakes such as unknown columns or text compared against
a number show up before you press Run.

**Columns**

| Operation | What it does | Runs on |
| --- | --- | --- |
| Select columns | Keep only the chosen columns, in that order | DuckDB SQL |
| Drop columns | Remove the chosen columns | DuckDB SQL |
| Rename columns | Rename one or more columns | DuckDB SQL |
| Change type | Convert columns to integer, number, text, boolean, date or timestamp | DuckDB SQL |
| Formula column | Add or replace a column computed with a SQL expression | DuckDB SQL |
| If / else column | Set a column's value from rules checked in order | DuckDB SQL |
| Combine columns | Join several columns into one text column | DuckDB SQL |

**Rows**

| Operation | What it does | Runs on |
| --- | --- | --- |
| Filter rows | Keep rows that match conditions | DuckDB SQL |
| Drop empty rows | Remove rows with missing values | DuckDB SQL |
| Fill empty values | Replace missing values with a value, statistic, or neighbouring row | DuckDB SQL |
| Remove duplicates | Keep one row per unique key (or remove fully identical rows) | DuckDB SQL |
| Sort | Order rows by one or more columns | DuckDB SQL |
| Top N / limit | Keep the first N rows, optionally the top N by a column | DuckDB SQL |
| Sample rows | Random sample by row count or percent (repeatable with a seed) | DuckDB SQL |

**Text**

| Operation | What it does | Runs on |
| --- | --- | --- |
| Clean text | Trim spaces, fix case and blank out empty strings | DuckDB SQL |
| Find and replace | Replace text or a regex pattern inside a column | DuckDB SQL |
| Split column | Split text by a delimiter into numbered columns | DuckDB SQL |
| Extract pattern | Pull a regex match (or capture group) into a new column | DuckDB SQL |

**Dates**

| Operation | What it does | Runs on |
| --- | --- | --- |
| Parse dates | Turn text into dates using a format (or automatic detection) | DuckDB SQL |
| Date parts | Add year, quarter, month, week, weekday... columns from a date | DuckDB SQL |
| Date difference | Days (or other units) between two date columns | DuckDB SQL |

**Combine** (connect one input to the box's *left* handle and one to *right*)

| Operation | What it does | Runs on |
| --- | --- | --- |
| Join | Inner, left, right, full, semi or anti join on matching key columns | DuckDB SQL |
| Union / append | Stack the rows of two inputs (by column name or position) | DuckDB SQL |

**Reshape**

| Operation | What it does | Runs on |
| --- | --- | --- |
| Group and aggregate | Group rows and compute sums, averages, counts... | DuckDB SQL |
| Pivot | Turn the values of one column into columns | DuckDB SQL |
| Unpivot | Turn columns into name/value rows (wide to long) | DuckDB SQL |
| Window / running calc | Rank, running totals, lag/lead and moving averages | DuckDB SQL |
| Bin / bucket | Group a number column into ranges | DuckDB SQL |

**Data quality**

| Operation | What it does | Runs on |
| --- | --- | --- |
| Profile data | One row per column: type, empties, distinct values, min/max, mean... | DuckDB SQL |
| Validate rules | Check rules per row: flag, drop, or fail the pipeline | DuckDB SQL |

**Prediction**

| Operation | What it does | Runs on |
| --- | --- | --- |
| Predict a number | Regression: linear, random forest or gradient boosting; adds a prediction column | Python |
| Predict a category | Classification: logistic regression, decision tree, random forest or KNN | Python |
| Cluster (k-means) | Group similar rows into k clusters; adds a cluster column | Python |
| Detect anomalies | Isolation forest; adds anomaly_score and is_anomaly | Python |
| Forecast | Time-series forecast (Holt-Winters or linear trend) with a confidence band | Python |

Regression and classification hold out a test share (default 20%) and report
R² / MAE / RMSE or accuracy / F1 / precision / recall with a confusion matrix
and the features that drive the prediction; rows with an empty target are
scored (`split = scored`). Results show in the Inspector after the run, and
each prediction's output is saved as its own dataset.

**Visualize / Custom**

| Operation | What it does | Runs on |
| --- | --- | --- |
| Dashboard | Table, histogram, pie and line chart for this data; opens in a new tab | Python |
| Custom (prompt) | Describe any transform in plain English; the local LLM writes the code | Local LLM |

---

## Dashboards

Drop a **Dashboard** box after any step and run the pipeline: the box gets an
**Open dashboard** button that opens a Plotly Dash page in a new browser tab
with headline numbers, a searchable, sortable, server-paged table, a
histogram, a pie chart and a line chart. Columns are picked automatically, or
set them in the box's settings (pie value, line series per group, page size).

Dashboard links carry a signed token that expires after 7 days, so they open
without logging in but can't be altered to read other data. The all-in-one
image generates the signing key on first start and keeps it in
`/data/secret_key`; set `SECRET_KEY` to use your own. Charts sample at most
200,000 rows; the table pages through all rows.

---

## Demo boards

[`examples/`](examples/) has a sample dataset
([`orders_demo_dataset.csv`](examples/orders_demo_dataset.csv), 120 orders)
and three boards to open with **Open** in the top bar. Then click the Source
box and upload the CSV.

| Board | What it shows |
| --- | --- |
| [`orders_demo_workflow.json`](examples/orders_demo_workflow.json) | Filter → aggregate → sort: revenue by region, no AI |
| [`orders_ml_demo.json`](examples/orders_ml_demo.json) | Fill missing values, predict order totals, flag unusual orders, forecast monthly revenue |
| [`orders_dashboard_demo.json`](examples/orders_dashboard_demo.json) | Filter out cancelled orders and open a dashboard |

---

## Two ways to run it

1. **Development stack** (`docker-compose.yml`) — every service in its own
   container with hot reload, MinIO, and Ollama. Best for hacking on the code.
2. **Single all-in-one image** (`deploy/Dockerfile.allinone`) — the whole
   product in one container with a filesystem object store, one port, one
   volume. Best for deploying anywhere. See [`deploy/README.md`](deploy/README.md).

---

## Build from scratch (development)

### Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Docker | 24+ | with Docker Compose v2 (`docker compose`) |
| Node.js | 20+ | only for running the frontend outside Docker |
| Python | 3.11 | only for running the backend outside Docker |
| RAM | ~8 GB free | for the full stack including Ollama |
| Ollama | latest | optional; enables full natural-language prompts |

### 1. Clone

```bash
git clone https://github.com/nirmalk960/prompt-etl.git
cd prompt-etl
```

### 2. Configure environment

```bash
cp .env.example .env
```

The defaults work out of the box for local development. `.env` is gitignored —
never commit real secrets. See [Configuration](#configuration) for the knobs.

### 3. Bring up the full stack

```bash
docker compose up --build
```

When it boots:

| Service     | URL                            | Notes                                          |
| ----------- | ------------------------------ | ---------------------------------------------- |
| Web UI      | http://localhost:5173          | Vite dev server with hot reload                |
| API         | http://localhost:8000          | OpenAPI docs at `/docs`                         |
| MinIO       | http://localhost:9001          | Console: `minioadmin` / `minioadmin`           |
| Postgres    | localhost:5432                 | `prompt_etl` / `prompt_etl` / `prompt_etl`     |
| Redis       | localhost:6379                 |                                                |
| Ollama      | http://localhost:11434         | `docker compose exec ollama ollama pull qwen2.5-coder:7b` |

Then open **Prompt ETL Studio** at http://localhost:5173:

1. Drag **Source** onto the canvas and upload CSV / JSON / Excel
   (presigned upload → Parquet ingest).
2. Drag operations from the palette (search, or browse by category), connect
   them, and fill in their settings in the **Inspector** — or switch a box to
   **Prompt** and describe the step.
3. Click **Run pipeline** — the step timeline and engine chip update live via SSE.
4. Add a **Write** node to export results locally (JSON / Parquet / CSV / Text).

`make help` lists convenience commands (`make up`, `make down`, `make logs`,
`make migrate`, `make test`).

### 4. Run backend or frontend on the host (optional)

Backend:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head            # apply DB migrations
uvicorn prompt_etl.main:app --reload --port 8000
# in another shell, start the worker:
arq prompt_etl.worker.settings.WorkerSettings
```

Frontend:

```bash
cd frontend
npm install
npm run dev                      # http://localhost:5173
```

Point the UI at a custom API host from the in-app **Settings → Server URL**
field (persisted in `localStorage`).

### 5. Database migrations

Migrations live in `backend/alembic/`. To create and apply one after model
changes:

```bash
cd backend
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

---

## Single-image deployment

Build and run the whole product as one container (no compose, no external
services):

```bash
# one-command installer (builds if needed, runs, waits for ready)
./scripts/install.sh
# -> http://localhost:8080
```

Or manually:

```bash
docker build -f deploy/Dockerfile.allinone -t prompt-etl:latest .
docker run -d --name prompt-etl \
  -p 8080:8080 -v prompt-etl-data:/data \
  --add-host=host.docker.internal:host-gateway \
  prompt-etl:latest
```

The container bundles Postgres, Redis, the API, the worker, and nginx (managed
by supervisord), auto-tunes worker concurrency and Postgres `shared_buffers` to
the RAM it's given, and persists all state in the `/data` volume. Full details:
[`deploy/README.md`](deploy/README.md).

The API requires an access token. The first boot writes one to
`/data/access_token`. Paste it into Settings in the app:

```bash
docker exec prompt-etl cat /data/access_token
```

`ALLOW_DEV_AUTH` stays unset on this image. Do not set it on a server other
people can reach. It turns login back into `Bearer dev:<email>`.

---

## LLM setup (Ollama)

Ollama is optional: every operation except **Custom** runs from its settings
without an LLM. Prompts also fall back to deterministic code generation for
simple phrasings when no LLM is available. For full natural-language
flexibility, run Ollama **on the host** (it gets GPU acceleration there, unlike
inside a container):

```bash
./scripts/setup_ollama.sh    # detects RAM, pulls the right model, prints the run flag
```

Model tiers: `qwen2.5-coder:7b` (< 14 GiB RAM), `:14b` (14–28 GiB),
`:32b` (≥ 28 GiB).

---

## Repo layout

```
prompt-etl/
├── backend/
│   ├── prompt_etl/
│   │   ├── main.py              # FastAPI app
│   │   ├── config.py            # pydantic-settings
│   │   ├── db/                  # SQLAlchemy async models + session
│   │   ├── routers/             # /datasets, /workflows, /jobs, /operations, /pipelines, ...
│   │   ├── services/            # object_store, ingest, ollama_client, pipeline, events
│   │   ├── ops/                 # operation catalog: SQL ops, ML ops, dashboard, runner
│   │   ├── dashboards/          # Plotly Dash app mounted at /dash + signed links
│   │   ├── engines/             # base, pandas, polars, duckdb, spark (stub), router
│   │   ├── worker/              # Arq worker entrypoint + tasks
│   │   └── schemas/             # Pydantic request/response models
│   ├── alembic/                 # DB migrations
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile               # multi-stage: shared base, api stage, worker stage
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # single-page Prompt ETL Studio canvas
│   │   ├── components/flow/     # React Flow nodes, bubble bar, inspector, timeline
│   │   ├── api/                 # typed client (datasets, jobs, SSE)
│   │   ├── lib/catalog.ts       # operation catalog, settings defaults and summaries
│   │   ├── lib/preflight.ts     # client-side pipeline validation
│   │   └── stores/              # Zustand workflow state
│   ├── Dockerfile
│   └── nginx.conf
├── examples/                    # demo dataset + demo boards
├── deploy/                      # single-image build (Dockerfile.allinone, nginx,
│                                #   supervisord, entrypoint, publish, compose)
├── charts/prompt-etl/           # Phase 1 Helm skeleton (Kubernetes)
├── docs/                        # ARCHITECTURE, PHASE2_SPARK, PHASE3_PRODUCTION
├── scripts/                     # install, setup_ollama, smoke tests, test data gen
├── docker-compose.yml           # local dev stack
├── .env.example
└── README.md
```

---

## Configuration

All settings come from environment variables (see `.env.example`). Key ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Host Ollama endpoint |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Model used for prompt-to-code |
| `OLLAMA_DISABLE` | `0` | Set `1` to force deterministic codegen only |
| `ARQ_MAX_JOBS` | auto (~1 per 2 GiB, 1–8) | Concurrent pipeline jobs |
| `ENGINE_SPARK_THRESHOLD_BYTES` | 50 GiB | Upper bound before jobs are refused (Phase 2: Spark) |
| `ACCESS_TOKEN` | generated into `/data/access_token` (all-in-one) | Required `Authorization: Bearer` secret. Paste it into Settings |
| `ALLOW_DEV_AUTH` | `false` | Accepts `Bearer dev:<email>`. Leave off on any shared or public server |
| `OPERATOR_EMAIL` | `you@local` | Workspace the access token signs in as |
| `SECRET_KEY` | generated into `/data/secret_key` (all-in-one) | Signs dashboard links. The default `dev-secret-change-me` is refused when `APP_ENV` is not local |
| `DASH_PATH_PREFIX` | `/api/dash/` | Public URL prefix of dashboards (`/dash/` when the API is served at the root) |

In the dev stack these are set by `docker-compose.yml`; in the single image the
entrypoint derives RAM-dependent values automatically.

---

## Testing

```bash
# backend unit tests (every operation, ML on synthetic data, dashboards)
cd backend && pytest

# end-to-end matrix against a running instance (configured ops, predictions,
# dashboards, preflight, prompt ops on every engine, export, lifecycle)
python .cursor/skills/testing-prompt-etl/scripts/run_matrix.py --api http://localhost:8080/api

# heavy-duty smoke tests (large synthetic datasets through the running stack)
./scripts/smoke_heavy.sh

# generate large test datasets
python scripts/generate_test_csv.py      # synthetic orders CSV
python scripts/generate_test_json.py     # ~10 GB JSONL for Polars/DuckDB
```

---

## Publishing

On every `vX.Y.Z` git tag, [`.github/workflows/publish-image.yml`](.github/workflows/publish-image.yml)
builds a multi-arch (`amd64` + `arm64`) image and pushes it to
`ghcr.io/nirmalk960/prompt-etl` using the built-in token. No extra secrets
are required:

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

After the package is public, anyone can run the current release with:

```bash
docker pull ghcr.io/nirmalk960/prompt-etl:0.2.1
```

To publish manually, see [`deploy/README.md`](deploy/README.md#publishing-to-github-container-registry-ghcr).

---

## What's implemented (Phase 1)

- [x] Object storage abstraction (S3 / MinIO / local filesystem)
- [x] Presigned multipart uploads (data never flows through the API)
- [x] CSV / JSON / XLSX → Parquet ingest via DuckDB streaming
- [x] Async SQLAlchemy + Alembic migrations
- [x] Arq job queue with Redis pubsub
- [x] Tiered EngineRouter (pandas / Polars / DuckDB) with cgroup-aware sizing
- [x] Per-engine LLM prompt templates + agent guard, with deterministic fallback
- [x] Client-side preflight validation with inline, actionable errors
- [x] Resumable SSE on `/jobs/{id}/events`
- [x] Step-level Parquet checkpointing
- [x] Prompt ETL Studio UI: drag-drop canvas, NL prompts, inspector, live timeline
- [x] Save/open pipelines, named workspaces, flexible local export
- [x] Docker Compose dev stack + single all-in-one image
- [x] 37-operation catalog that runs without an LLM (sandboxed DuckDB SQL + Python)
- [x] Two-input joins and unions, server-side schema preview for preflight
- [x] Predictions: regression, classification, clustering, anomalies, forecasting
- [x] Dashboards (Plotly Dash) opened from the canvas via signed links

### Stubbed (Phase 2 / 3)

- [ ] `SparkEngine` submitting `SparkApplication` CRs to the Spark K8s Operator
- [ ] Multi-tenant auth (OIDC), RBAC, workspace quotas
- [ ] OpenTelemetry + Prometheus + Grafana + Loki
- [ ] Native source connectors (Postgres / Snowflake / Kafka)

See [`docs/PHASE2_SPARK.md`](docs/PHASE2_SPARK.md) and
[`docs/PHASE3_PRODUCTION.md`](docs/PHASE3_PRODUCTION.md) for the design.

---

## Contributing

Bug reports, ideas, and pull requests are welcome. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for setup, the PR checklist, and how the
ops catalog is extended.

## Security

How to report vulnerabilities, the single-operator threat model, and hardening
defaults live in [`SECURITY.md`](SECURITY.md). Please use a private advisory
for security findings, not a public Issue.

## License

[MIT](LICENSE)
