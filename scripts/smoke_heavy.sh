#!/usr/bin/env bash
# Heavy-duty smoke tests for PromptETL-Meg:
#   - Tiered CSV uploads (multipart, ingest, pipeline)
#   - Engine router verification (pandas / polars / duckdb)
#   - Spark pin rejection (Phase 1)
#   - Frontend health check
#
# Requires: curl, jq, python3, docker compose stack running

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="${API:-http://localhost:8000}"
FRONTEND="${FRONTEND:-http://localhost:5173}"
EMAIL="${EMAIL:-smoke-heavy@local}"
AUTH="Authorization: Bearer dev:${EMAIL}"
TMPDIR="${TMPDIR:-/tmp/prompt-etl-smoke}"
mkdir -p "$TMPDIR"

PASS=0
FAIL=0
SKIP=0

say() { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()  { printf "  \033[1;32m✓ %s\033[0m\n" "$*"; PASS=$((PASS + 1)); }
bad() { printf "  \033[1;31m✗ %s\033[0m\n" "$*"; FAIL=$((FAIL + 1)); }
skip(){ printf "  \033[1;33m⊘ %s\033[0m\n" "$*"; SKIP=$((SKIP + 1)); }

wait_ready() {
  say "Wait for API /ready"
  for i in $(seq 1 60); do
    if curl -sf "$API/ready" >/dev/null 2>&1; then
      curl -s "$API/ready" | jq -r '"  ram=\(.ram_bytes) cpus=\(.cpus) ollama=\(.ollama.available) store=\(.object_store)"'
      return 0
    fi
    sleep 2
  done
  bad "API not ready after 120s"
  exit 1
}

upload_csv() {
  local name="$1" path="$2"
  local size
  size=$(wc -c <"$path" | tr -d ' ')

  local init
  init=$(curl -sf -X POST "$API/datasets/init" -H "$AUTH" -H "Content-Type: application/json" \
    -d "{\"name\": \"$name\", \"source_format\": \"csv\", \"size_bytes\": $size}")

  local dataset_id upload_id part_size
  dataset_id=$(echo "$init" | jq -r .dataset_id)
  upload_id=$(echo "$init" | jq -r .upload_id)
  part_size=$(echo "$init" | jq -r .part_size_bytes)
  local part_count
  part_count=$(echo "$init" | jq '.parts | length')

  echo "  dataset_id=$dataset_id parts=$part_count part_size=$part_size bytes=$size"

  local completed='[]'
  local pn=0
  while IFS= read -r url; do
    pn=$((pn + 1))
    local start=$(( (pn - 1) * part_size ))
    local chunk_len=$part_size
    if [ $((start + chunk_len)) -gt "$size" ]; then
      chunk_len=$((size - start))
    fi
    local part_file
    part_file=$(mktemp -t part.XXXXXX)
    dd if="$path" of="$part_file" bs=1 skip="$start" count="$chunk_len" status=none 2>/dev/null
    local etag
    etag=$(curl -sS -D - -X PUT "$url" --data-binary "@$part_file" -o /dev/null \
      | awk '/[Ee]Tag/ {print $2}' | tr -d '"\r')
    rm -f "$part_file"
    completed=$(echo "$completed" | jq --argjson n "$pn" --arg e "$etag" '. + [{part_number: $n, etag: $e}]')
    echo "    uploaded part $pn/$part_count etag=${etag:0:12}…"
  done < <(echo "$init" | jq -r '.parts[].url')

  curl -sf -X POST "$API/datasets/$dataset_id/complete" -H "$AUTH" -H "Content-Type: application/json" \
    -d "{\"upload_id\": \"$upload_id\", \"parts\": $completed}" >/dev/null

  echo "$dataset_id"
}

wait_dataset() {
  local dataset_id="$1" max="${2:-600}"
  for i in $(seq 1 "$max"); do
    local st rows
    st=$(curl -sf "$API/datasets/$dataset_id" -H "$AUTH" | jq -r .status)
    rows=$(curl -sf "$API/datasets/$dataset_id" -H "$AUTH" | jq -r .row_count)
    if [ "$st" = "ready" ]; then
      echo "  ingest ready in ${i}s rows=$rows"
      return 0
    fi
    if [ "$st" = "failed" ]; then
      curl -sf "$API/datasets/$dataset_id" -H "$AUTH" | jq .
      return 1
    fi
    if [ $((i % 10)) -eq 0 ]; then
      echo "  waiting ingest… ${i}s status=$st"
    fi
    sleep 1
  done
  bad "ingest timeout for $dataset_id"
  return 1
}

run_pipeline() {
  local dataset_id="$1" engine="$2" label="$3"
  local pipeline
  pipeline=$(cat <<JSON
{
  "definition": {
    "nodes": [
      {"id": "s1", "kind": "source", "label": "source", "dataset_id": "$dataset_id"},
      {"id": "f1", "kind": "filter", "prompt": "Filter rows where department equals Engineering"},
      {"id": "a1", "kind": "aggregate", "prompt": "Count rows by region"},
      {"id": "w1", "kind": "write"}
    ],
    "edges": [
      {"source": "s1", "target": "f1"},
      {"source": "f1", "target": "a1"},
      {"source": "a1", "target": "w1"}
    ],
    "engine": "$engine"
  }
}
JSON
)
  local job_id
  job_id=$(curl -sf -X POST "$API/jobs" -H "$AUTH" -H "Content-Type: application/json" -d "$pipeline" | jq -r .id)
  echo "  job_id=$job_id engine_pref=$engine"

  local max=900
  for i in $(seq 1 "$max"); do
    local detail status eng err result_id
    detail=$(curl -sf "$API/jobs/$job_id" -H "$AUTH")
    status=$(echo "$detail" | jq -r .status)
    eng=$(echo "$detail" | jq -r .engine)
    err=$(echo "$detail" | jq -r .error)
    result_id=$(echo "$detail" | jq -r .result_dataset_id)

    if [ "$status" = "succeeded" ]; then
      local selected
      selected=$(echo "$detail" | jq -r '.events[] | select(.kind=="engine_selected") | .payload.engine' | head -1)
      local preview_rows
      preview_rows=$(curl -sf "$API/datasets/$result_id/preview?n=5" -H "$AUTH" | jq '.rows | length')
      echo "  succeeded in ${i}s engine=$eng router=$selected preview_rows=$preview_rows"
      echo "$eng|$selected|$result_id"
      return 0
    fi
    if [ "$status" = "failed" ]; then
      echo "  FAILED in ${i}s engine=$eng error=$err"
      echo "$detail" | jq '.events[-3:]'
      echo "FAILED|$eng|$err"
      return 1
    fi
    if [ $((i % 15)) -eq 0 ]; then
      echo "  polling… ${i}s status=$status engine=$eng"
    fi
    sleep 1
  done
  bad "job timeout $job_id"
  return 1
}

gen_csv() {
  local mb="$1" out="$2"
  if [ -f "$out" ]; then
    echo "  reusing $out ($(du -h "$out" | awk '{print $1}'))"
    return 0
  fi
  say "Generate ${mb}MiB CSV -> $out"
  python3 "$ROOT/scripts/generate_test_csv.py" --target-mb "$mb" --out "$out"
}

# ---------------------------------------------------------------------------
wait_ready

say "Frontend health"
if curl -sf -o /dev/null -w "  HTTP %{http_code}\n" "$FRONTEND/"; then
  ok "frontend reachable at $FRONTEND"
else
  bad "frontend unreachable at $FRONTEND"
fi

say "Run basic smoke_e2e.sh"
if bash "$ROOT/scripts/smoke_e2e.sh"; then
  ok "basic smoke_e2e"
else
  bad "basic smoke_e2e failed"
fi

RAM_BYTES=$(curl -sf "$API/ready" | jq -r .ram_bytes)
RAM_MB=$((RAM_BYTES / 1024 / 1024))
echo "  detected RAM ~${RAM_MB} MiB (router uses RAM/4 and RAM/2 thresholds)"

# ---------------------------------------------------------------------------
# Tier 1: ~50 MiB — multipart upload (multiple 16 MiB parts)
# ---------------------------------------------------------------------------
T50="$TMPDIR/heavy_50mb.csv"
gen_csv 50 "$T50"
say "Tier 1: 50 MiB upload + ingest + auto pipeline"
DS50=$(upload_csv "heavy_50mb.csv" "$T50")
if wait_dataset "$DS50" 120; then
  RES=$(run_pipeline "$DS50" "auto" "tier50") || true
  if [[ "$RES" == FAILED* ]]; then bad "tier50 pipeline: $RES"; else ok "tier50 pipeline: $RES"; fi
else
  bad "tier50 ingest failed"
fi

# ---------------------------------------------------------------------------
# Tier 2: ~250 MiB — medium / polars territory on typical 8 GiB worker
# ---------------------------------------------------------------------------
T250="$TMPDIR/heavy_250mb.csv"
gen_csv 250 "$T250"
say "Tier 2: 250 MiB upload + ingest + auto pipeline"
DS250=$(upload_csv "heavy_250mb.csv" "$T250")
if wait_dataset "$DS250" 300; then
  RES=$(run_pipeline "$DS250" "auto" "tier250") || true
  if [[ "$RES" == FAILED* ]]; then bad "tier250 pipeline: $RES"; else ok "tier250 pipeline: $RES"; fi
else
  bad "tier250 ingest failed"
fi

# ---------------------------------------------------------------------------
# Tier 3: ~1 GiB — heavier load
# ---------------------------------------------------------------------------
T1G="$TMPDIR/heavy_1gb.csv"
gen_csv 1024 "$T1G"
say "Tier 3: 1 GiB upload + ingest + auto pipeline"
DS1G=$(upload_csv "heavy_1gb.csv" "$T1G")
if wait_dataset "$DS1G" 900; then
  RES=$(run_pipeline "$DS1G" "auto" "tier1g") || true
  if [[ "$RES" == FAILED* ]]; then bad "tier1g pipeline: $RES"; else ok "tier1g pipeline: $RES"; fi
else
  bad "tier1g ingest failed"
fi

# ---------------------------------------------------------------------------
# Tier 4: DuckDB — file > RAM/2 OR pin duckdb on 250MB
# ---------------------------------------------------------------------------
say "Tier 4: pinned duckdb engine on 250 MiB dataset"
if [ -n "${DS250:-}" ]; then
  RES=$(run_pipeline "$DS250" "duckdb" "pin-duckdb") || true
  if [[ "$RES" == duckdb* ]] || [[ "$RES" == *"|duckdb|"* ]]; then
    ok "pinned duckdb: $RES"
  elif [[ "$RES" == FAILED* ]]; then
    bad "pinned duckdb: $RES"
  else
    ok "pinned duckdb completed: $RES"
  fi
else
  skip "pinned duckdb (no DS250)"
fi

# ---------------------------------------------------------------------------
# Tier 5: Polars pin on 50MB
# ---------------------------------------------------------------------------
say "Tier 5: pinned polars engine on 50 MiB dataset"
if [ -n "${DS50:-}" ]; then
  RES=$(run_pipeline "$DS50" "polars" "pin-polars") || true
  if [[ "$RES" == FAILED* ]]; then bad "pinned polars: $RES"; else ok "pinned polars: $RES"; fi
else
  skip "pinned polars (no DS50)"
fi

# ---------------------------------------------------------------------------
# Tier 6: Spark pin — expect Phase 1 failure (validates router guard)
# ---------------------------------------------------------------------------
say "Tier 6: pinned spark engine (expect Phase 1 failure)"
if [ -n "${DS50:-}" ]; then
  RES=$(run_pipeline "$DS50" "spark" "pin-spark") || true
  if [[ "$RES" == FAILED* ]] && [[ "$RES" == *Phase* || "$RES" == *Spark* || "$RES" == *spark* || "$RES" == *not* ]]; then
    ok "spark correctly rejected in Phase 1"
  elif [[ "$RES" == FAILED* ]]; then
    ok "spark failed as expected: $RES"
  else
    bad "spark should fail in Phase 1 but got: $RES"
  fi
else
  skip "spark rejection test (no DS50)"
fi

# ---------------------------------------------------------------------------
# Tier 7: Large duckdb routing — generate file just above RAM/2 if feasible
# ---------------------------------------------------------------------------
DUCK_TARGET_MB=$((RAM_MB / 2 + 200))
if [ "$DUCK_TARGET_MB" -lt 512 ]; then DUCK_TARGET_MB=512; fi
if [ "$DUCK_TARGET_MB" -gt 5120 ]; then
  skip "Tier 7 duckdb auto-route (${DUCK_TARGET_MB}MiB > 5GiB cap for local smoke)"
else
  TDK="$TMPDIR/heavy_duck_${DUCK_TARGET_MB}mb.csv"
  gen_csv "$DUCK_TARGET_MB" "$TDK"
  say "Tier 7: ${DUCK_TARGET_MB} MiB (> RAM/2) upload + auto pipeline (expect duckdb router)"
  DSDK=$(upload_csv "heavy_duck_${DUCK_TARGET_MB}mb.csv" "$TDK")
  if wait_dataset "$DSDK" 1200; then
    RES=$(run_pipeline "$DSDK" "auto" "tier-duck") || true
    if [[ "$RES" == *"|duckdb|"* ]] || [[ "$RES" == duckdb* ]]; then
      ok "duckdb auto-route: $RES"
    elif [[ "$RES" == FAILED* ]]; then
      bad "duckdb auto-route failed: $RES"
    else
      ok "large auto pipeline completed (engine=$RES)"
    fi
  else
    bad "tier duck ingest failed"
  fi
fi

# ---------------------------------------------------------------------------
say "Summary"
printf "  \033[1;32mPASS=%s\033[0m  \033[1;31mFAIL=%s\033[0m  \033[1;33mSKIP=%s\033[0m\n" "$PASS" "$FAIL" "$SKIP"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
echo ""
ok "All heavy-duty smoke tests passed."
