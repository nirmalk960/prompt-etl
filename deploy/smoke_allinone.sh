#!/usr/bin/env bash
# End-to-end smoke test for the single-image deployment.
#
# Boots a throwaway container from prompt-etl:latest, then exercises:
#   upload (chunked local-store parts) -> ingest -> polars pipeline -> export
#
# Usage: ./deploy/smoke_allinone.sh [image]
set -euo pipefail

IMAGE="${1:-prompt-etl:latest}"
PORT="${PORT:-8090}"
NAME="prompt-etl-smoke"
VOLUME="prompt-etl-smoke-data"
API="http://localhost:$PORT/api"
AUTH="Authorization: Bearer dev:smoke@local"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=$(mktemp -d -t prompt-etl-smoke)

PASS=0; FAIL=0
ok()  { printf '  \033[1;32mPASS\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad() { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
say "Start container ($IMAGE on port $PORT)"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker volume rm "$VOLUME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p "$PORT:8080" -v "$VOLUME:/data" \
  --add-host=host.docker.internal:host-gateway "$IMAGE" >/dev/null

for i in $(seq 1 60); do
  curl -fsS "$API/ready" >/dev/null 2>&1 && break
  [ "$i" = 60 ] && { bad "API not ready in 120s"; docker logs "$NAME" | tail -40; exit 1; }
  sleep 2
done
READY=$(curl -fsS "$API/ready")
echo "$READY" | jq -r '"  ram=\(.ram_bytes) cpus=\(.cpus) store=\(.object_store) ollama=\(.ollama.available)"'
[ "$(echo "$READY" | jq -r .object_store)" = "local" ] && ok "local object store active" || bad "expected local store"

say "Frontend served by nginx"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/")
[ "$CODE" = "200" ] && ok "UI responds 200" || bad "UI returned $CODE"

# ---------------------------------------------------------------------------
MB="${MB:-5}"
say "Generate + upload ${MB} MiB CSV through chunked local-store parts"
CSV="$TMP/smoke.csv"
python3 "$ROOT/scripts/generate_test_csv.py" --target-mb "$MB" --out "$CSV" >/dev/null
SIZE=$(wc -c <"$CSV" | tr -d ' ')

INIT=$(curl -fsS -X POST "$API/datasets/init" -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"name\": \"smoke.csv\", \"source_format\": \"csv\", \"size_bytes\": $SIZE}")
DATASET_ID=$(echo "$INIT" | jq -r .dataset_id)
UPLOAD_ID=$(echo "$INIT" | jq -r .upload_id)
PART_SIZE=$(echo "$INIT" | jq -r .part_size_bytes)
echo "  dataset=$DATASET_ID parts=$(echo "$INIT" | jq '.parts|length') part_size=$PART_SIZE"

COMPLETED='[]'
PN=0
while IFS= read -r URL; do
  PN=$((PN+1))
  # Local-store URLs are API-relative.
  case "$URL" in http*) TARGET="$URL" ;; *) TARGET="$API$URL" ;; esac
  START=$(( (PN-1) * PART_SIZE ))
  LEN=$PART_SIZE
  [ $((START+LEN)) -gt "$SIZE" ] && LEN=$((SIZE-START))
  dd if="$CSV" of="$TMP/part" bs=1048576 skip=$((START/1048576)) count=$(( (LEN+1048575)/1048576 )) status=none
  truncate -s "$LEN" "$TMP/part" 2>/dev/null || python3 -c "import sys;f=open('$TMP/part','r+b');f.truncate($LEN)"
  ETAG=$(curl -fsS -D - -X PUT "$TARGET" --data-binary "@$TMP/part" -o /dev/null \
    | awk 'tolower($1) ~ /^etag:/ {print $2}' | tr -d '"\r')
  [ -n "$ETAG" ] || { bad "no ETag for part $PN"; exit 1; }
  COMPLETED=$(echo "$COMPLETED" | jq --argjson n "$PN" --arg e "$ETAG" '. + [{part_number:$n, etag:$e}]')
done < <(echo "$INIT" | jq -r '.parts[].url')
ok "uploaded $PN part(s)"

curl -fsS -X POST "$API/datasets/$DATASET_ID/complete" -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"upload_id\": \"$UPLOAD_ID\", \"parts\": $COMPLETED}" >/dev/null
ok "multipart completed"

for i in $(seq 1 120); do
  ST=$(curl -fsS "$API/datasets/$DATASET_ID" -H "$AUTH" | jq -r .status)
  [ "$ST" = "ready" ] && { ok "ingest ready (${i}s)"; break; }
  [ "$ST" = "failed" ] && { bad "ingest failed"; curl -fsS "$API/datasets/$DATASET_ID" -H "$AUTH" | jq .; exit 1; }
  [ "$i" = 120 ] && { bad "ingest timeout"; exit 1; }
  sleep 1
done

# ---------------------------------------------------------------------------
say "Run polars pipeline (filter + aggregate)"
JOB=$(curl -fsS -X POST "$API/jobs" -H "$AUTH" -H "Content-Type: application/json" -d @- <<JSON
{
  "definition": {
    "nodes": [
      {"id": "s1", "kind": "source", "label": "source", "dataset_id": "$DATASET_ID"},
      {"id": "f1", "kind": "filter", "prompt": "Filter rows where department equals Engineering"},
      {"id": "a1", "kind": "aggregate", "prompt": "sum of salary by region"},
      {"id": "w1", "kind": "write"}
    ],
    "edges": [
      {"source": "s1", "target": "f1"},
      {"source": "f1", "target": "a1"},
      {"source": "a1", "target": "w1"}
    ],
    "engine": "polars"
  }
}
JSON
)
JOB_ID=$(echo "$JOB" | jq -r .id)
echo "  job=$JOB_ID"

RESULT_ID=""
for i in $(seq 1 300); do
  DETAIL=$(curl -fsS "$API/jobs/$JOB_ID" -H "$AUTH")
  ST=$(echo "$DETAIL" | jq -r .status)
  if [ "$ST" = "succeeded" ]; then
    RESULT_ID=$(echo "$DETAIL" | jq -r .result_dataset_id)
    ok "pipeline succeeded (${i}s, engine=$(echo "$DETAIL" | jq -r .engine))"
    break
  fi
  if [ "$ST" = "failed" ]; then
    bad "pipeline failed: $(echo "$DETAIL" | jq -r .error)"
    exit 1
  fi
  [ "$i" = 300 ] && { bad "pipeline timeout"; exit 1; }
  sleep 1
done

# ---------------------------------------------------------------------------
say "Export result as CSV"
curl -fsS "$API/datasets/$RESULT_ID/export?fmt=csv" -H "$AUTH" -o "$TMP/result.csv"
LINES=$(wc -l <"$TMP/result.csv" | tr -d ' ')
head -5 "$TMP/result.csv" | sed 's/^/  /'
[ "$LINES" -ge 2 ] && ok "export produced $LINES line(s)" || bad "export looks empty"

# ---------------------------------------------------------------------------
echo
printf '\033[1mSmoke result: %d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
