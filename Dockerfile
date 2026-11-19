# ---------- base: Python + the app's runtime dependencies ----------
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

# An unprivileged user to run as (least privilege). A fixed numeric uid lets platforms
# such as Kubernetes (runAsNonRoot) verify it is not root without looking up the name.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid 10001 --no-create-home app

# Installed as root, so the packages are not writable by the runtime user.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- test: base + test tools; runs pytest (docker compose run --rm --build test) ----------
FROM base AS test

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

# Owned by the test user: pytest and Hypothesis create their caches in /code. (WORKDIR made
# /code itself as root, and COPY --chown only covers the copied files, so chown the directory too.)
COPY --chown=10001:10001 . .
RUN chown 10001:10001 /code

USER 10001

CMD ["pytest"]

# ---------- app: the API server (the default target, so `docker build .` builds this) ----------
FROM base AS app

# Owned by root and only readable by the app user: a compromised process can't rewrite its own code.
COPY . .

EXPOSE 8000

USER 10001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
