#!/usr/bin/env bash
# Helper to migrate WikiWare data from MongoDB to Postgres.
# - Always takes a mongodump backup first.
# - Exports MongoDB collections to NDJSON for inspection.
# - Optionally loads the NDJSON into Postgres staging tables as JSONB.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ./scripts/mongo_to_postgres_migration.sh [--load-postgres] [--yes]

  --load-postgres  Load exported Mongo documents into Postgres staging tables (jsonb).
  --yes, -y        Skip confirmation prompts.
  --help, -h       Show this help message.

The script reads MONGODB_URL, MONGODB_DB_NAME, and POSTGRES_DSN from .env if present.
Defaults:
  MONGODB_URL=mongodb://localhost:27017
  MONGODB_DB_NAME=wikiware
  POSTGRES_DSN=postgresql://postgres:postgres@localhost:5432/wikiware
USAGE
}

log() {
  printf '[mongo->postgres] %s\n' "$*"
}

fail() {
  printf '[mongo->postgres] ERROR: %s\n' "$*" >&2
  exit 1
}

confirm() {
  local prompt="${1:-Proceed?} [y/N] "
  if [[ ${AUTO_CONFIRM:-0} -eq 1 ]]; then
    return 0
  fi
  read -r -p "$prompt" reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

require_cmd() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || fail "Missing required command: $name"
}

uri_with_db() {
  local uri="$1"
  local db="$2"
  if [[ "$uri" =~ /${db}($|[\?#/]) ]]; then
    echo "$uri"
    return
  fi
  if [[ "$uri" == *"?"* ]]; then
    local base="${uri%%\?*}"
    local query="${uri#"$base"}"
    echo "${base%/}/$db${query}"
  else
    echo "${uri%/}/$db"
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -o allexport
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +o allexport
fi

MONGODB_URL="${MONGODB_URL:-mongodb://localhost:27017}"
MONGODB_DB_NAME="${MONGODB_DB_NAME:-wikiware}"
POSTGRES_DSN="${POSTGRES_DSN:-postgresql://postgres:postgres@localhost:5432/wikiware}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
EXPORT_DIR="${EXPORT_DIR:-$ROOT_DIR/migration_workdir}"
MONGO_EXPORT_DIR="$EXPORT_DIR/mongo_exports"
CSV_EXPORT_DIR="$EXPORT_DIR/postgres_csv"
TIMESTAMP="$(date -u +"%Y-%m-%dT%H%M%SZ")"
AUTO_CONFIRM=0
LOAD_POSTGRES=0

for arg in "$@"; do
  case "$arg" in
    --load-postgres) LOAD_POSTGRES=1 ;;
    --yes|-y) AUTO_CONFIRM=1 ;;
    --help|-h) usage; exit 0 ;;
    *) fail "Unknown argument: $arg" ;;
  esac
done

MONGODB_URI_WITH_DB="$(uri_with_db "$MONGODB_URL" "$MONGODB_DB_NAME")"

backup_mongo() {
  require_cmd mongodump
  mkdir -p "$BACKUP_DIR"
  local archive="$BACKUP_DIR/mongodb-${MONGODB_DB_NAME}-${TIMESTAMP}.archive.gz"

  log "Creating MongoDB backup at $archive"
  mongodump \
    --uri="$MONGODB_URI_WITH_DB" \
    --archive="$archive" \
    --gzip
  log "Backup complete: $archive"
}

export_collections() {
  require_cmd mongoexport
  mkdir -p "$MONGO_EXPORT_DIR"
  local collections=(
    pages
    history
    branches
    users
    sessions
    image_hashes
    analytics_events
  )

  log "Exporting MongoDB collections to $MONGO_EXPORT_DIR"
  for collection in "${collections[@]}"; do
    local outfile="$MONGO_EXPORT_DIR/${collection}.ndjson"
    log "  - $collection -> $outfile"
    mongoexport \
      --uri="$MONGODB_URI_WITH_DB" \
      --collection="$collection" \
      --type=json \
      --out="$outfile"
  done
  log "Exports complete."
}

load_postgres_staging() {
  require_cmd perl
  require_cmd psql
  mkdir -p "$CSV_EXPORT_DIR"
  local collections=(
    pages
    history
    branches
    users
    sessions
    image_hashes
    analytics_events
  )

  log "Preparing Postgres staging tables in schema wikiware_migration"
  psql "$POSTGRES_DSN" -v ON_ERROR_STOP=1 <<'SQL'
CREATE SCHEMA IF NOT EXISTS wikiware_migration;
SQL

  for collection in "${collections[@]}"; do
    local ndjson="$MONGO_EXPORT_DIR/${collection}.ndjson"
    if [[ ! -s "$ndjson" ]]; then
      log "  - Skipping $collection (no export found)"
      continue
    fi

    local csv="$CSV_EXPORT_DIR/${collection}.csv"
    perl -pe 's/"/""/g; $_="\"$_\""' "$ndjson" >"$csv"

    log "  - Loading $collection into Postgres staging table"
    psql "$POSTGRES_DSN" -v ON_ERROR_STOP=1 <<SQL
SET client_min_messages TO WARNING;
CREATE TABLE IF NOT EXISTS wikiware_migration.${collection}_raw (
  doc jsonb
);
TRUNCATE wikiware_migration.${collection}_raw;
\\copy wikiware_migration.${collection}_raw (doc) FROM '${csv}' WITH (FORMAT csv);
SQL
  done
  log "Postgres staging load complete."
}

main() {
  require_cmd date

  log "Using MongoDB URI: $MONGODB_URI_WITH_DB"
  log "Using Postgres DSN: $POSTGRES_DSN"

  if ! confirm "Create backup and export collections?"; then
    log "Aborted by user."
    exit 1
  fi

  backup_mongo
  export_collections

  if [[ $LOAD_POSTGRES -eq 1 ]]; then
    if ! confirm "Load NDJSON exports into Postgres staging tables?"; then
      log "Skipping Postgres load."
    else
      load_postgres_staging
    fi
  else
    log "Postgres load skipped (use --load-postgres to enable)."
  fi

  log "Done. Backup + exports are in:"
  log "  - $BACKUP_DIR"
  log "  - $MONGO_EXPORT_DIR"
  if [[ $LOAD_POSTGRES -eq 1 ]]; then
    log "  - $CSV_EXPORT_DIR (temp CSV used for staging loads)"
  fi
}

main "$@"
