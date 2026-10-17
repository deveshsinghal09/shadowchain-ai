#!/bin/sh
set -eu
: "${FALKORDB_PASSWORD:?Set the database password in Railway Variables}"
# Railway volume is mounted at /data. No public domain or TCP proxy is required.
exec redis-server --bind 0.0.0.0 :: --protected-mode yes \
  --port 6379 --dir /data --appendonly yes --appendfsync everysec \
  --save '60 1' --maxmemory-policy noeviction \
  --requirepass "$FALKORDB_PASSWORD" \
  --loadmodule "$FALKORDB_BIN_PATH/falkordb.so" THREAD_COUNT 2 TIMEOUT_DEFAULT 2000 TIMEOUT_MAX 30000
