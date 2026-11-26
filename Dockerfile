# syntax=docker/dockerfile:1
# The API image, which also runs fakepay. Day to day you run the API on Windows with
# `uvicorn --reload`; this image is for `docker compose up` and the load tests.

FROM python:3.12-slim

# Copy the uv binary from its official image instead of installing it with pip.
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies first, code second. Docker caches each step, so editing app code does not
# reinstall every package; only a change to pyproject.toml or uv.lock does.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY app ./app
COPY fakepay ./fakepay
# The migrate service in docker-compose.yml runs `alembic upgrade head` from this same image.
COPY alembic.ini ./
COPY migrations ./migrations

# Never run the server as root inside the container. /data is where fakepay keeps its
# SQLite file; a new volume mounted there takes over this owner.
RUN useradd --create-home --uid 10001 appuser \
    && install -d -o appuser /data
USER appuser

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
