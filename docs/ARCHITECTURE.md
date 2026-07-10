# Architecture

PromptETL-Meg separates **control plane** (API + DB + queue) from **data plane**
(worker pods + engines + object storage). The API never reads or writes user
data; it only mints presigned URLs and enqueues jobs. This is the single most
important property for scaling to large datasets.

## Request lifecycle: upload → run → result

```mermaid
sequenceDiagram
  autonumber
  participant U as User (browser)
  participant API as FastAPI
  participant S3 as S3 / MinIO
  participant Q as Redis (queue + pubsub)
  participant DB as Postgres
  participant W as Arq worker
  participant E as EngineRouter

  U->>API: POST /datasets/init { name, size_bytes }
  API->>DB: insert datasets row (status=uploading)
  API->>S3: create_multipart_upload
  API-->>U: { dataset_id, upload_id, presigned parts }
  loop per part
    U->>S3: PUT part (direct)
    S3-->>U: ETag
  end
  U->>API: POST /datasets/{id}/complete { parts }
  API->>S3: complete_multipart_upload
  API->>Q: enqueue ingest_dataset(dataset_id)
  W->>S3: download raw
  W->>S3: upload parquet (DuckDB COPY streaming)
  W->>DB: dataset.status = ready

  U->>API: POST /jobs { pipeline definition }
  API->>DB: insert jobs row (status=queued)
  API->>Q: enqueue run_pipeline_job(job_id)
  API-->>U: { job_id }

  U->>API: GET /jobs/{id}/events (SSE)
  API->>Q: SUBSCRIBE job:{id}
  W->>E: router.decide(bytes, node_kinds, pinned)
  E-->>W: engine = pandas | polars | duckdb | spark
  W->>DB: job.engine = ...
  W->>Q: PUBLISH engine_selected
  Q-->>API: event
  API-->>U: SSE engine_selected

  loop per node
    W->>S3: download inputs (parquet)
    W->>E: execute_step(code, inputs) -> parquet out
    W->>S3: upload step checkpoint
    W->>DB: insert job_events row
    W->>Q: PUBLISH step_completed
    Q-->>API: event
    API-->>U: SSE step_completed (preview)
  end

  W->>S3: copy final checkpoint -> result_dataset.parquet
  W->>DB: insert result Dataset row, job.status = succeeded
  W->>Q: PUBLISH job_succeeded
  Q-->>API: event
  API-->>U: SSE job_succeeded
```

## EngineRouter

The router decides one engine per pipeline run (see
[backend/prompt_etl/engines/router.py](../backend/prompt_etl/engines/router.py)):

```mermaid
flowchart TD
  Start([decide]) --> Pinned{pinned\nin definition?}
  Pinned -->|yes| UsePinned([return pinned])
  Pinned -->|no| Spark{bytes >= spark_threshold?}
  Spark -->|yes| UseSpark([return spark])
  Spark -->|no| Halfish{bytes > RAM/2?}
  Halfish -->|yes| UseDuckdb1([return duckdb out-of-core])
  Halfish -->|no| Quart{bytes > RAM/4\nAND all nodes\nSQL-friendly?}
  Quart -->|yes| UseDuckdb2([return duckdb])
  Quart -->|no| MidSize{bytes > RAM/4?}
  MidSize -->|yes| UsePolars([return polars lazy])
  MidSize -->|no| UsePandas([return pandas])
```

RAM is detected at startup from cgroups v2 (`/sys/fs/cgroup/memory.max`), with
fallbacks to cgroups v1, `/proc/meminfo`, and `sysctl hw.memsize` on macOS. An
explicit `ENGINE_RAM_OVERRIDE_BYTES` always wins (useful for tests).

## Engine contract

Every engine implements
[`DataEngine`](../backend/prompt_etl/engines/base.py):

```text
async def execute_step(
    *, node_id, node_kind, code,
    input_object_keys: list[str],   # parquet keys in object storage
    output_object_key: str,         # parquet key to write
    config: dict | None,
) -> StepResult
```

All intermediates are Parquet on object storage. This means a `polars` step
can feed a `duckdb` step in a subsequent run without conversion — and it means
checkpoints survive the worker process.

## Why DuckDB for ingest specifically

A 30 GB CSV would OOM pandas, take minutes in Polars, and require a Spark
cluster. DuckDB's vectorized streaming `COPY (SELECT ...) TO ... (FORMAT
PARQUET)` runs in bounded memory on a single core, spills to disk when needed,
and produces a properly compressed Parquet with row-group statistics — which
the EngineRouter later uses to size Spark executors in Phase 2.

## Resumable SSE

`GET /jobs/{id}/events` accepts both `?after=<seq>` and the standard
`Last-Event-ID` HTTP header. On (re)connect, the API:

1. Reads persisted `job_events` rows with `seq > after` from Postgres (backfill).
2. Subscribes to the Redis pubsub channel `job:{id}` for live events.
3. Yields each event with `id: <seq>` so the browser's `EventSource` can resume
   correctly on the next reconnect.

This is what makes the UI's "Live progress" panel survive a browser refresh
during a 30-minute run.
