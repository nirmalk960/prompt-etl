# Prompt ETL — Single-Image Deployment

One Docker image containing the whole application: web UI, API, worker,
Postgres, Redis, and a filesystem object store. One exposed port, one data
volume. Runs anywhere Docker runs (Linux, macOS, Windows via Docker Desktop,
amd64 and arm64).

## Quick start

```bash
# from the repo root
./scripts/install.sh
# -> http://localhost:8080
```

Or manually:

```bash
docker build -f deploy/Dockerfile.allinone -t prompt-etl:latest .

docker run -d --name prompt-etl \
  -p 8080:8080 \
  -v prompt-etl-data:/data \
  --add-host=host.docker.internal:host-gateway \
  prompt-etl:latest
```

## LLM setup (optional but recommended)

Prompts fall back to deterministic code generation when no LLM is available.
For full natural-language flexibility, install Ollama **on the host** (it
gets GPU acceleration there, unlike inside a container) and let the setup
script pick the right model for your RAM:

```bash
./scripts/setup_ollama.sh
```

Model tiers: `qwen2.5-coder:7b` (< 14 GiB RAM), `:14b` (14–28 GiB),
`:32b` (≥ 28 GiB). Pass the printed `-e OLLAMA_MODEL=...` flag to
`docker run` (or set `OLLAMA_MODEL` for `install.sh`).

## How it scales with the machine

The container adapts to wherever it is deployed:

- The **engine router** reads the container's cgroup memory limit and picks
  pandas / Polars / DuckDB per job based on data size vs available RAM
  (out-of-core DuckDB for data larger than RAM/2).
- The **entrypoint** auto-tunes worker concurrency (`ARQ_MAX_JOBS`, ~1 job
  per 2 GiB) and Postgres `shared_buffers` (RAM/8) on every boot.
- Give the container more memory (`docker run -m 16g ...` or a bigger host)
  and all tuning follows automatically — no config changes.

## Configuration

All settings are env vars (`-e KEY=value` on `docker run`):

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Host Ollama endpoint |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Model used for prompt-to-code |
| `OLLAMA_DISABLE` | `0` | Set `1` to force deterministic codegen only |
| `ARQ_MAX_JOBS` | auto (RAM/2 GiB, 1–8) | Concurrent pipeline jobs |
| `ENGINE_SPARK_THRESHOLD_BYTES` | 50 GiB | Upper bound before jobs are refused (Phase 2: Spark) |

Persistent state (Postgres data, uploaded datasets, results) lives in the
`/data` volume. Back it up by snapshotting the Docker volume.

## Operations

```bash
docker logs -f prompt-etl          # all services (nginx, api, worker, pg, redis)
docker exec prompt-etl supervisorctl status
docker restart prompt-etl          # safe: state is in /data
docker rm -f prompt-etl            # remove (volume survives)
docker volume rm prompt-etl-data   # full reset
```

Health endpoint: `GET /api/ready` — reports detected RAM/CPUs, Ollama
availability, and the active model.

## Publishing to GitHub Container Registry (GHCR)

Goal: one published image that anyone with access pulls and runs with a single
command, on Intel or ARM, without building anything. Because this repo is
**private**, the GHCR package is private too — only accounts you grant access to
can pull it.

### One-time setup

1. Create a GitHub Personal Access Token (classic) with the `write:packages`
   scope at https://github.com/settings/tokens (add `read:packages` for pulling).
2. Log in to GHCR locally:

```bash
echo "<your-token>" | docker login ghcr.io -u nirmalk960 --password-stdin
```

### Publish (manual, multi-arch)

```bash
NAMESPACE=nirmalk960 VERSION=0.1.0 ./deploy/publish.sh
```

This builds `linux/amd64` + `linux/arm64` and pushes
`ghcr.io/nirmalk960/prompt-etl:0.1.0`, `:0.1`, and `:latest`. The script creates
a `docker-container` buildx builder automatically.

### Publish (automated via GitHub Actions)

[`.github/workflows/publish-image.yml`](../.github/workflows/publish-image.yml)
publishes to GHCR on every `vX.Y.Z` git tag using the built-in `GITHUB_TOKEN` —
**no secrets to configure**. The workflow needs `packages: write` permission
(already set in the file). Then:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

The published package appears under your repo's **Packages** and inherits its
private visibility.

### What your users run

Once published, anyone you've granted access to pulls and runs it with (after
`docker login ghcr.io` with a `read:packages` token):

```bash
docker run -d --name prompt-etl \
  -p 8080:8080 -v prompt-etl-data:/data \
  --add-host=host.docker.internal:host-gateway \
  ghcr.io/nirmalk960/prompt-etl:latest
# open http://localhost:8080
```

A ready-made [`deploy/docker-compose.public.yml`](docker-compose.public.yml)
lets users `docker compose up` without remembering flags.

## When to use the Helm chart instead

This image scales **vertically** (more RAM/CPU on one machine). For
horizontal scaling — multiple workers, external Postgres/S3, autoscaling —
use the Kubernetes chart in [`charts/prompt-etl-meg`](../charts/prompt-etl-meg)
with the per-service images built by [`docker-compose.yml`](../docker-compose.yml).
