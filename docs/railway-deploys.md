# Deploying without downtime

Monitoring showed the site down for 5–10 minutes on every deploy. The cause was the
`Procfile`, which ran the whole release inside the web process:

    web: python manage.py migrate && python manage.py collectstatic --no-input && gunicorn ...

Railway keeps the old container serving while the new one starts, but the new one is not
*ready* until its start command answers a health check — and that command was doing a
migration and a full static upload first. Meanwhile Railway may stop the old container.

## The three phases, and which one to use

| Phase | Railway setting | Runs | Old container |
| --- | --- | --- | --- |
| Build | Settings → Build → **Custom Build Command** | while building the image | still serving |
| Pre-deploy | Settings → Deploy → **Pre-Deploy Command** | after build, before switchover | **still serving** |
| Start | Settings → Deploy → **Custom Start Command**, or `Procfile` | the long-running process | replaced |

Railway does **not** call it a release command, which is why it is hard to find: it is the
**Pre-Deploy Command**, under Deploy, below the start command.

## What to set

**Pre-Deploy Command:**

    python manage.py migrate --no-input && python manage.py collectstatic --no-input

**Healthcheck Path:**

    /healthz

**Start command** — already in the `Procfile`, nothing to type:

    web: gunicorn eatart.asgi:application -k uvicorn.workers.UvicornWorker --workers ${WEB_CONCURRENCY:-3} --access-logfile - --error-logfile -

Leave the Build Command alone.

## Migrations must be backwards-compatible

This is the part that bites later. During pre-deploy the **old code runs against the new
schema** — that is the whole point, since the old container is what keeps the site up.

Additive changes are safe. A migration that removes something the old code still reads will
500 the live site for the length of the deploy. So dropping a field is **two deploys**:

1. Stop using it in code, and deploy.
2. Drop the column, and deploy.

Renames are the same shape: add, backfill, switch, then remove.

## Why deploys were slow, separately from being down

`django-storages` cannot read modification times from S3, so a plain `collectstatic`
re-uploaded **every** static file on every deploy whether it had changed or not. With 187
files it is minutes of uploads to achieve nothing.

`Collectfasta` compares checksums against S3 and skips what is unchanged. It has to be
listed **before `django.contrib.staticfiles`** in `INSTALLED_APPS`, because it overrides the
`collectstatic` command and app order decides whose version wins.

    COLLECTFASTA_STRATEGY = 'collectfasta.strategies.boto3.Boto3Strategy'
    COLLECTFASTA_ENABLED = USE_S3_STATIC

Disabled when static files are not on S3: locally `collectstatic` is a directory copy and
already instant, and pretending otherwise would only hide problems.

## /healthz

`eatart/views/health.py`. It returns `ok` and touches **nothing** — no database, no cache,
no storage.

That is deliberate. A health check that queried the database would conflate two different
failures: a momentary database blip would make Railway decide a good build was broken and
roll back a deploy that was fine, at the worst possible moment to be wrong.

`healthcheck.railway.app` is in `ALLOWED_HOSTS` because that is the `Host` Railway's check
sends. Without it Django answers **400 Bad Request**, the check never passes, and the deploy
silently never goes live — with `Invalid HTTP_HOST header` as the only clue, in a log line
that was itself invisible until `django.request` was lowered to `WARNING`. If a deploy ever
hangs in "waiting for health check", look there first.

## If a spec field changes

An image spec change renames every derived file, and the running site 404s its images until
they exist. That is a separate operation from the deploy — see
[image-colour.md](image-colour.md) and `scripts/estimate_image_regen.py`.
