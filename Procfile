web: gunicorn eatart.asgi:application -k uvicorn.workers.UvicornWorker --workers ${WEB_CONCURRENCY:-3} --access-logfile - --error-logfile -
