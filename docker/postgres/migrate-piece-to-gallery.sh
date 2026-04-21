#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${DEV_ENV_FILE:-${ROOT_DIR}/.env.local}"
SQL_FILE="${ROOT_DIR}/docker/postgres/migrate-piece-to-gallery.sql"

if [[ ! -f "${ENV_FILE}" && -f "${ROOT_DIR}/.env" ]]; then
    ENV_FILE="${ROOT_DIR}/.env"
fi

if [[ -f "${ENV_FILE}" ]]; then
    set -a
    source "${ENV_FILE}"
    set +a
fi

COMPOSE_ARGS=()
if [[ -f "${ENV_FILE}" ]]; then
    COMPOSE_ARGS+=(--env-file "${ENV_FILE}")
fi

POSTGRES_DB="${POSTGRES_DB:-eatart}"
POSTGRES_USER="${POSTGRES_USER:-eatart}"

cd "${ROOT_DIR}"

docker compose "${COMPOSE_ARGS[@]}" up -d db

until docker compose "${COMPOSE_ARGS[@]}" exec -T db pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; do
    echo "Waiting for database..."
done

docker compose "${COMPOSE_ARGS[@]}" exec -T db psql \
    -v ON_ERROR_STOP=1 \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    < "${SQL_FILE}"