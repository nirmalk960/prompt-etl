# Prompt ETL test reference

## Matrix contents

Fixture: 250 deterministic order rows (240 unique + 10 exact duplicates), with every 10th `discount_amount` null. Columns: `order_id, customer_id, order_status, order_date, order_total, discount_amount, region`. Expected values are computed from the fixture in Python and compared with the exported CSV.

| Category | What it proves |
|---|---|
| platform | `/health`, `/ready`, UI served by nginx, dev auth works, unauthenticated requests rejected |
| ingest | CSV, NDJSON, XLSX, Parquet all ingest to Parquet with correct row count, schema, preview; multipart upload with several parts |
| ops | Each deterministic operation on each pinned engine: filter (=, !=, >, <), select, rename, drop nulls (one column / any), dedupe (key / full row), aggregate (simple, aliased, month()), sort (asc/desc) |
| chains | Demo pipeline (filter -> aggregate -> sort), cleaning pipeline (5 steps), fan-out DAG |
| export | csv / json (NDJSON) / txt (tab) / parquet downloads, bad format 422, unknown dataset 404 |
| routing | `auto` picks and reports an engine; `spark` is rejected with a clear error |
| validation | Bad DAGs return 422; missing columns and unparseable prompts fail with a message instead of hanging or succeeding |
| lifecycle | Job list/detail/events, SSE replay, cancel, workflow CRUD, concurrent jobs |
| llm | fill_nulls, custom column, natural-language filter and aggregate (needs Ollama) |
| scale | Generated N MiB CSV through upload, ingest, and the auto-routed pipeline, with timings (opt-in) |
| persistence | Data survives a container restart (opt-in) |

## Deterministic prompt grammar

Without Ollama, only these phrasings work (from `backend/prompt_etl/services/operation_templates.py`). Use them when writing new deterministic checks.

| Kind | Phrasing |
|---|---|
| filter | `Filter rows where <col> equals <v>` / `not equals` / `<col> > <n>` / `<col> < <n>` |
| select | `Keep columns a, b, c` |
| rename | `Rename column <old> to <new>` |
| drop_nulls | `Drop rows with null values [in <col>]` |
| deduplicate | `Remove duplicates on <cols>` / `Remove duplicate rows` |
| aggregate | `Sum of <col> by <cols>`; `Group by <cols> and calculate sum(<col>) as <alias>, ...`; `month(<date_col>)` as a group key |
| sort | `Sort by <col> ascending/descending` (single column) |

`fill_nulls`, `custom`, and phrasings like `order_total is greater than 50` need Ollama.

## Known failure signatures

| Symptom in report | Where to look |
|---|---|
| `[duckdb] deduplicate on key keeps all columns` fails with columns dropped | `_dedup` duckdb branch uses `SELECT DISTINCT <cols>` |
| `[duckdb] rename column` fails, `region` still present | `_rename` duckdb branch uses `SELECT * REPLACE (old AS new)`, which keeps the old name |
| `[duckdb] drop nulls in any column` row count too high | `_drop_nulls` duckdb branch returns `SELECT * FROM df` when no column is given |
| `aggregate by month()` missing `total_revenue` | `_AGG_METRIC` alias capture includes preceding words, then `_clean_metric_alias` |
| Pinned engine mismatch | engine router override in `backend/prompt_etl/engines/router.py` |
| Job stuck until timeout | worker crashed: `start_test_container.sh logs`, look for tracebacks in `worker` |
| `storage returned no ETag` | `routers/uploads.py` or nginx stripping headers |
| SSE test sees no events | nginx `proxy_buffering` for `/api/jobs/*/events` in `deploy/nginx.conf` |

## UI checklist

Run against the test container with the browser tools. Upload `orders_2025.csv` from the workspace root where a file is needed.

- [ ] App loads; top bar shows backend healthy, RAM/CPU, Ollama status, engine
- [ ] Settings: change server URL, reload, value persists; reset it
- [ ] Workspace name edits persist across reload
- [ ] Drag each bubble onto the canvas; connect nodes; change edge style
- [ ] Source upload shows progress, then a schema/preview in the Inspector
- [ ] Preflight: an aggregate that renames columns lets a downstream sort use the new names without an error
- [ ] Preflight: a sort on a non-existent column shows an inline Inspector issue (no alert popup) and blocks the run
- [ ] Run pipeline: step timeline and node glow progress live; engine chip updates
- [ ] Failure path: a failing step turns red in both canvas and timeline (no amber left behind)
- [ ] Stop button cancels a running job
- [ ] Write node: each format (CSV/JSON/Parquet/Text); auto-export downloads exactly one file named after the workspace
- [ ] Export now button works after a run
- [ ] Save produces `<workspace>.json`; New clears the board; Open restores nodes, edges, and name
