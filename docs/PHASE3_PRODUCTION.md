# Phase 3 — Multi-tenant production hardening

This is the design (not yet implemented) for taking PromptETL-Meg from a
self-hosted single-tenant deployment to a multi-tenant SaaS or enterprise
self-hosted offering.

## Multi-tenancy and RBAC

- Replace dev-token auth ([backend/prompt_etl/deps.py](../backend/prompt_etl/deps.py))
  with OIDC via `authlib` or a managed gateway (Auth0 / Cognito / Keycloak).
- Add `organizations` table; `workspaces.organization_id` FK; `memberships`
  table with role enum (`owner`, `admin`, `editor`, `viewer`).
- Enforce row-level security in Postgres:
  - `ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;`
  - Policy: `USING (workspace_id IN (SELECT id FROM workspaces WHERE organization_id = current_setting('app.org_id')::uuid))`
  - Set `app.org_id` per request via a SQLAlchemy event listener.
- Object storage: per-workspace key prefix `workspaces/{ws_id}/...` (already
  the format) + bucket policies that deny cross-prefix reads.

## Quotas and billing hooks

- `quotas` table per workspace: `max_concurrent_jobs`, `max_dataset_bytes`,
  `max_executors`, `max_job_runtime_seconds`.
- Enforce on:
  - dataset init (size cap)
  - job enqueue (concurrent cap; reject with 429)
  - Spark application submit (executor cap)
- Emit usage events to a `usage_events` table and (optionally) to Kafka /
  Kinesis for downstream billing.

## Secrets and source connectors

- New table `integrations`: `{id, workspace_id, kind, encrypted_config, ...}`.
- Encrypt with envelope encryption: per-row DEK encrypted by a workspace KEK
  managed by KMS (AWS KMS / GCP KMS / Vault Transit).
- Add `source` node `kind` variants:
  - `source_postgres` — JDBC scan, optionally with `partition_column` for parallelism
  - `source_snowflake` — Snowflake JDBC + COPY UNLOAD to S3 for big snapshots
  - `source_kafka` — bounded read from offsets X..Y, materialized to Parquet
  - `source_bigquery` — BigQuery Storage Read API
- Each connector is implemented as a worker task that produces a `Dataset`
  row, so the rest of the pipeline machinery doesn't need to change.

## Observability

- **Tracing**: OpenTelemetry SDK in the FastAPI app and worker. Propagate
  trace context through the Arq job payload (custom serializer). Spark driver
  reports stage spans via a small Listener (similar to progress_listener).
- **Metrics**: `/metrics` endpoint on api and worker via `prometheus_fastapi_instrumentator`
  and a Prometheus exporter in the worker; key metrics:
  - `prompt_etl_jobs_total{status, engine}`
  - `prompt_etl_job_duration_seconds{engine}` histogram
  - `prompt_etl_engine_router_decision_total{engine, reason}` (small set)
  - `prompt_etl_ingest_throughput_bytes_per_second`
  - Arq queue depth, Redis pubsub fanout latency
- **Logs**: structlog already emits JSON in non-local environments; ship to
  Loki / OpenSearch / CloudWatch.
- **Dashboards**: Grafana JSON checked in under `observability/grafana/`.

## Resilience

- **Idempotent jobs**: accept `Idempotency-Key` header on `POST /jobs`; store
  on `jobs.idempotency_key` with unique index per workspace.
- **Step-level resume**: on a worker crash, the next worker that picks up the
  job can re-run only the steps whose output Parquet doesn't exist. Add a
  `step_states` table keyed by `(job_id, node_id)` with the last successful
  checkpoint.
- **Spark executor failures**: rely on Spark's task retry; cap retries to
  avoid infinite loops; mark the job failed with diagnostics.
- **Object storage backpressure**: retry uploads with exponential backoff
  (already via `tenacity`); circuit-break the worker on 5xx storm.

## Deployment

- Helm chart at [charts/prompt-etl-meg/](../charts/prompt-etl-meg/) gains:
  - `values.yaml` knobs for `replicaCount`, `resources`, `autoscaling`,
    `ingress.tls`, `oidc.{issuer, audience, clientId}`, `objectStore.{kind, ...}`
  - Subchart deps: `bitnami/postgresql`, `bitnami/redis`, `minio/minio`
    (optional), `spark-operator/spark-operator` (Phase 2)
  - NetworkPolicies isolating api, worker, and spark namespaces
  - PodDisruptionBudgets and HPA on `api` (CPU) and `worker` (queue depth via
    custom metric `arq_queue_depth`)
- CI/CD: GitHub Actions matrix per service builds multi-arch images,
  publishes Helm chart on tag, and runs an E2E smoke against `kind`.

## Migration sequencing

1. Land OIDC + workspaces + RLS behind a feature flag (dev tokens still work).
2. Add quotas (default very high) and emit usage events.
3. Ship Helm chart + Postgres/Redis subcharts; document self-hosted install.
4. Add OpenTelemetry + Prometheus; ship Grafana dashboards.
5. Add idempotency + step-level resume.
6. Add source connectors one at a time; each behind a per-workspace toggle.
