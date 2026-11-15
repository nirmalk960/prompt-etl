#!/usr/bin/env bash
# End-to-end smoke test:
#   1. Create a small CSV in-memory
#   2. Initialize a multipart upload
#   3. PUT the part to MinIO
#   4. Complete the upload (triggers ingest)
#   5. Wait for the dataset to become "ready"
#   6. Run a pipeline (Source -> Filter -> Write)
#   7. Poll the job until succeeded
#   8. Print the result preview
#
# Requires: curl, jq

set -euo pipefail

API="${API:-http://localhost:8000}"
EMAIL="${EMAIL:-smoke@local}"
if [ -n "${ACCESS_TOKEN:-}" ]; then AUTH="Authorization: Bearer ${ACCESS_TOKEN}"; else AUTH="Authorization: Bearer dev:${EMAIL}"; fi

say() { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }

say "Health"
curl -s "$API/ready" | jq -r '"  ram=\(.ram_bytes) ollama=\(.ollama.available) store=\(.object_store)"'

say "Create sample CSV"
CSV_PATH=$(mktemp -t emp.XXXXXX.csv)
cat >"$CSV_PATH" <<'EOF'
id,name,department,salary
1,Alice,Engineering,95000
2,Bob,Sales,70000
3,Carol,Engineering,105000
4,Dan,Marketing,80000
5,Eve,Engineering,120000
6,Frank,Sales,65000
7,Grace,Engineering,110000
EOF
SIZE=$(wc -c <"$CSV_PATH" | tr -d ' ')
echo "  $CSV_PATH ($SIZE bytes)"

say "Init upload"
INIT=$(curl -s -X POST "$API/datasets/init" -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"name\": \"smoke_emp.csv\", \"source_format\": \"csv\", \"size_bytes\": $SIZE}")
echo "$INIT" | jq .
DATASET_ID=$(echo "$INIT" | jq -r .dataset_id)
UPLOAD_ID=$(echo "$INIT" | jq -r .upload_id)
PART_URL=$(echo "$INIT" | jq -r .parts[0].url)

say "PUT part 1"
ETAG=$(curl -sS -D - -X PUT "$PART_URL" --data-binary "@$CSV_PATH" -o /dev/null | awk '/[Ee]Tag/ {print $2}' | tr -d '"\r')
echo "  ETag=$ETAG"

say "Complete upload"
curl -s -X POST "$API/datasets/$DATASET_ID/complete" -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"upload_id\": \"$UPLOAD_ID\", \"parts\": [{\"part_number\": 1, \"etag\": \"$ETAG\"}]}" | jq .

say "Wait for ingest"
for i in $(seq 1 30); do
  STATUS=$(curl -s "$API/datasets/$DATASET_ID" -H "$AUTH" | jq -r .status)
  echo "  attempt $i: status=$STATUS"
  [ "$STATUS" = "ready" ] && break
  [ "$STATUS" = "failed" ] && { curl -s "$API/datasets/$DATASET_ID" -H "$AUTH" | jq; exit 1; }
  sleep 1
done

say "Preview"
curl -s "$API/datasets/$DATASET_ID/preview?n=10" -H "$AUTH" | jq .

say "Submit pipeline (filter dept=Engineering)"
PIPELINE=$(cat <<JSON
{
  "definition": {
    "nodes": [
      {"id": "s1", "kind": "source", "label": "emp.csv", "dataset_id": "$DATASET_ID"},
      {"id": "f1", "kind": "filter", "prompt": "Filter rows where department equals Engineering"},
      {"id": "w1", "kind": "write"}
    ],
    "edges": [
      {"source": "s1", "target": "f1"},
      {"source": "f1", "target": "w1"}
    ],
    "engine": "auto"
  }
}
JSON
)
JOB=$(curl -s -X POST "$API/jobs" -H "$AUTH" -H "Content-Type: application/json" -d "$PIPELINE")
echo "$JOB" | jq .
JOB_ID=$(echo "$JOB" | jq -r .id)

say "Poll job"
for i in $(seq 1 30); do
  STATUS=$(curl -s "$API/jobs/$JOB_ID" -H "$AUTH" | jq -r .status)
  ENGINE=$(curl -s "$API/jobs/$JOB_ID" -H "$AUTH" | jq -r .engine)
  echo "  attempt $i: status=$STATUS engine=$ENGINE"
  [ "$STATUS" = "succeeded" ] && break
  [ "$STATUS" = "failed" ] && { curl -s "$API/jobs/$JOB_ID" -H "$AUTH" | jq; exit 1; }
  sleep 1
done

say "Job events"
curl -s "$API/jobs/$JOB_ID" -H "$AUTH" | jq '.events[] | {seq, kind, payload: (.payload | if .preview then .preview.row_count else . end)}'

say "Result dataset"
RESULT_ID=$(curl -s "$API/jobs/$JOB_ID" -H "$AUTH" | jq -r .result_dataset_id)
echo "  result_dataset_id=$RESULT_ID"
curl -s "$API/datasets/$RESULT_ID/preview?n=20" -H "$AUTH" | jq .

say "Smoke test complete."
