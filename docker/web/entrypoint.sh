#!/bin/sh
set -e

echo "Waiting for database..."
until pg_isready -h db -p 5432 -U "${POSTGRES_USER:-eatart}" -d "${POSTGRES_DB:-eatart}" >/dev/null 2>&1; do
  sleep 2
done

echo "Applying migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Starting gunicorn..."
exec gunicorn eatart.wsgi:application --bind 0.0.0.0:8000 --workers 3 --reload
