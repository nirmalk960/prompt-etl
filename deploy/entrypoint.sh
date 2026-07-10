#!/usr/bin/env bash
# Prompt ETL single-image entrypoint.
#   1. Detect the container memory budget (cgroup v2 -> v1 -> /proc/meminfo).
#   2. Auto-tune worker concurrency and Postgres shared_buffers from it.
#   3. First boot: initdb + create role/database.
#   4. Run alembic migrations.
#   5. Hand off to supervisord (postgres, redis, api, worker, nginx).
set -euo pipefail

PGDATA="${PGDATA:-/data/postgres}"
PGBIN="${PGBIN:-/usr/lib/postgresql/15/bin}"
PGUSER="${POSTGRES_USER:-prompt_etl}"
PGPASSWORD_VAL="${POSTGRES_PASSWORD:-prompt_etl}"
PGDB="${POSTGRES_DB:-prompt_etl}"

mkdir -p /data/objects /data/redis /run/postgresql
chown -R postgres:postgres /run/postgresql

# ---------------------------------------------------------------------------
# RAM detection (mirrors backend/prompt_etl/engines/resources.py)
# ---------------------------------------------------------------------------
detect_ram_bytes() {
  local v
  if [ -f /sys/fs/cgroup/memory.max ]; then
    v=$(cat /sys/fs/cgroup/memory.max)
    if [ "$v" != "max" ]; then echo "$v"; return; fi
  fi
  if [ -f /sys/fs/cgroup/memory/memory.limit_in_bytes ]; then
    v=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)
    if [ "$v" -lt 4611686018427387904 ] 2>/dev/null; then echo "$v"; return; fi
  fi
  # printf avoids awk's scientific notation for large numbers
  awk '/MemTotal:/ {printf "%.0f", $2 * 1024; exit}' /proc/meminfo
}

RAM_BYTES=$(detect_ram_bytes)
RAM_MB=$((RAM_BYTES / 1048576))
echo "[entrypoint] detected memory budget: ${RAM_MB} MiB"

# Worker concurrency: ~1 job per 2 GiB, clamped to [1, 8].
if [ -z "${ARQ_MAX_JOBS:-}" ]; then
  ARQ_MAX_JOBS=$((RAM_MB / 2048))
  [ "$ARQ_MAX_JOBS" -lt 1 ] && ARQ_MAX_JOBS=1
  [ "$ARQ_MAX_JOBS" -gt 8 ] && ARQ_MAX_JOBS=8
  export ARQ_MAX_JOBS
fi
echo "[entrypoint] ARQ_MAX_JOBS=${ARQ_MAX_JOBS}"

# Postgres shared_buffers: RAM/8, clamped to [128 MiB, 4096 MiB].
SHARED_BUFFERS_MB=$((RAM_MB / 8))
[ "$SHARED_BUFFERS_MB" -lt 128 ] && SHARED_BUFFERS_MB=128
[ "$SHARED_BUFFERS_MB" -gt 4096 ] && SHARED_BUFFERS_MB=4096

# ---------------------------------------------------------------------------
# Postgres first-boot init
# ---------------------------------------------------------------------------
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  echo "[entrypoint] initializing Postgres data directory at $PGDATA"
  mkdir -p "$PGDATA"
  chown -R postgres:postgres "$PGDATA"
  chmod 700 "$PGDATA"
  su -s /bin/bash postgres -c "$PGBIN/initdb -D $PGDATA --encoding=UTF8 --auth-local=trust --auth-host=md5"
fi
chown -R postgres:postgres "$PGDATA"

# Tuning overrides (rewritten every boot so RAM changes take effect).
cat > "$PGDATA/postgresql.auto.conf" <<EOF
listen_addresses = '127.0.0.1'
port = 5432
shared_buffers = ${SHARED_BUFFERS_MB}MB
max_connections = 50
EOF
chown postgres:postgres "$PGDATA/postgresql.auto.conf"

echo "[entrypoint] starting Postgres for bootstrap/migrations"
su -s /bin/bash postgres -c "$PGBIN/pg_ctl -D $PGDATA -w -t 60 start -l /tmp/pg-bootstrap.log"

# Bootstrap over the Unix socket (trust auth); TCP is md5 for the app role.
PSQL="$PGBIN/psql -U postgres -v ON_ERROR_STOP=1"
if ! su -s /bin/bash postgres -c "$PSQL -tAc \"SELECT 1 FROM pg_roles WHERE rolname='$PGUSER'\"" | grep -q 1; then
  echo "[entrypoint] creating role $PGUSER"
  su -s /bin/bash postgres -c "$PSQL -c \"CREATE ROLE $PGUSER LOGIN PASSWORD '$PGPASSWORD_VAL'\""
fi
if ! su -s /bin/bash postgres -c "$PSQL -tAc \"SELECT 1 FROM pg_database WHERE datname='$PGDB'\"" | grep -q 1; then
  echo "[entrypoint] creating database $PGDB"
  su -s /bin/bash postgres -c "$PSQL -c \"CREATE DATABASE $PGDB OWNER $PGUSER\""
fi

echo "[entrypoint] running migrations"
(cd /opt/prompt-etl/backend && alembic upgrade head)

su -s /bin/bash postgres -c "$PGBIN/pg_ctl -D $PGDATA -w stop"

echo "[entrypoint] starting services"
exec supervisord -c /etc/supervisor/supervisord.conf
