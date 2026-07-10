# Prompt ETL Studio

A server-deployable, prompt-driven ETL platform. Build data pipelines on a
drag-and-drop canvas, describe each step in natural language, and watch live
progress stream back — backed by object storage, an async job queue, and a
tiered compute router that picks the right engine for the data size.

> **Status:** private preview. This repository is the primary codebase.

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
- [Two ways to run it](#two-ways-to-run-it)
- [Build from scratch (development)](#build-from-scratch-development)
- [Single-image deployment](#single-image-deployment)
- [LLM setup (Ollama)](#llm-setup-ollama)
- [Repo layout](#repo-layout)
- [Configuration](#configuration)
- [Testing](#testing)
- [Publishing](#publishing-a-private-image)
- [What's implemented](#whats-implemented-phase-1)
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
2. Drag operations (Filter, Select, Sort, Aggregate, …), connect nodes, and
   write prompts in the **Inspector**.
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

---

## LLM setup (Ollama)

Prompts fall back to deterministic code generation when no LLM is available, so
the app works without Ollama for common operations. For full natural-language
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
│   │   ├── routers/             # /datasets, /workflows, /jobs, /uploads, /health
│   │   ├── services/            # object_store, ingest, ollama_client, pipeline, events
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
│   │   ├── lib/preflight.ts     # client-side pipeline validation
│   │   └── stores/              # Zustand workflow state
│   ├── Dockerfile
│   └── nginx.conf
├── deploy/                      # single-image build (Dockerfile.allinone, nginx,
│                                #   supervisord, entrypoint, publish, compose)
├── charts/prompt-etl-meg/       # Phase 1 Helm skeleton (Kubernetes)
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

In the dev stack these are set by `docker-compose.yml`; in the single image the
entrypoint derives RAM-dependent values automatically.

---

## Testing

```bash
# backend unit tests
cd backend && pytest

# heavy-duty smoke tests (large synthetic datasets through the running stack)
./scripts/smoke_heavy.sh

# generate large test datasets
python scripts/generate_test_csv.py      # synthetic orders CSV
python scripts/generate_test_json.py     # ~10 GB JSONL for Polars/DuckDB
```

---

## Publishing (a private image)

This repo is private; the recommended registry is **GitHub Container Registry
(GHCR)**, whose package inherits the repo's private visibility. On every
`vX.Y.Z` git tag, [`.github/workflows/publish-image.yml`](.github/workflows/publish-image.yml)
builds a multi-arch (`amd64` + `arm64`) image and pushes it to
`ghcr.io/nirmalk960/prompt-etl` using the built-in token — no secrets to set up:

```bash
git tag v0.1.0 && git push origin v0.1.0
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

### Stubbed (Phase 2 / 3)

- [ ] `SparkEngine` submitting `SparkApplication` CRs to the Spark K8s Operator
- [ ] Multi-tenant auth (OIDC), RBAC, workspace quotas
- [ ] OpenTelemetry + Prometheus + Grafana + Loki
- [ ] Native source connectors (Postgres / Snowflake / Kafka)

See [`docs/PHASE2_SPARK.md`](docs/PHASE2_SPARK.md) and
[`docs/PHASE3_PRODUCTION.md`](docs/PHASE3_PRODUCTION.md) for the design.

---

## License

MIT
