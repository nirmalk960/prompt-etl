---
name: testing-prompt-etl
description: Run the full Prompt ETL release test matrix against a running or freshly pulled container - every ingest format, every operation on every engine (pandas/Polars/DuckDB), multi-step pipelines, exports, routing, validation errors, job lifecycle, LLM prompts, scale, persistence, and a UI pass. Use before a release, after a new image is published, or when asked to test the app completely.
disable-model-invocation: true
---

# Testing Prompt ETL

Release gate for Prompt ETL. Tests drive the public API like the UI does and assert on the **output data**, not just job status.

## Workflow

```
- [ ] 1. Start an isolated test container
- [ ] 2. Run the API matrix (core)
- [ ] 3. Run the LLM pass (if Ollama is up)
- [ ] 4. Run scale + persistence (opt-in, slower)
- [ ] 5. UI pass in the browser
- [ ] 6. Report and tear down
```

Paths below are relative to this skill directory.

### 1. Start an isolated test container

Never test against the user's real container or volume.

```bash
bash scripts/start_test_container.sh up                       # ghcr.io/nirmalk960/prompt-etl:latest on :8090
bash scripts/start_test_container.sh up prompt-etl:latest     # a locally built image instead
```

If a pull from GHCR fails with `denied`, test the locally built image instead. Do not pipe tokens through the shell.

For a deterministic run without Ollama, start with `OLLAMA_DISABLE=1`.

### 2. Run the API matrix (core)

```bash
python3 scripts/run_matrix.py --api http://localhost:8090/api --report /tmp/prompt-etl-report.json
```

Covers: platform, ingest (CSV/NDJSON/XLSX/Parquet), ops (15 operations x 3 engines), chains, export, routing, validation, lifecycle. Takes a few minutes.

Useful flags: `--engines duckdb`, `--only ops,chains`, `--skip llm`, `--fail-fast`. Set `MATRIX_DEBUG=1` for tracebacks.

### 3. LLM pass

The `llm` category runs automatically when `/ready` reports Ollama available, and is skipped otherwise. It covers `fill_nulls`, `custom`, and natural-language filter/aggregate, which the deterministic codegen cannot handle. If the user wants it and it was skipped, check that Ollama is running on the host with the configured model pulled.

### 4. Scale and persistence (opt-in)

```bash
python3 scripts/run_matrix.py --api http://localhost:8090/api --only scale,persistence \
  --heavy-mb 50,500 --container prompt-etl-test
```

The persistence test restarts the container and checks that datasets, jobs and contents survive. Only pass `--container` for the throwaway test container.

### 5. UI pass

Follow the checklist in [reference.md](reference.md#ui-checklist) with the browser tools against `http://localhost:8090`. The API matrix cannot see frontend-only behaviour: preflight messages, the step timeline, save/open, and auto-export.

### 6. Report and tear down

Read the JSON report and summarise:

- Counts per category and the overall verdict (release-ready only with zero FAIL in core categories).
- Each FAIL: the check name, the engine, the failure detail, and the likely cause. Use [reference.md](reference.md#known-failure-signatures) to map symptoms to code.
- SKIPs and why (for example, no Ollama), so the user knows what was not covered.

Do not fix code during the test run. Report first, then offer fixes.

```bash
bash scripts/start_test_container.sh down
```

## Additional resources

- Matrix contents, UI checklist, failure signatures: [reference.md](reference.md)
