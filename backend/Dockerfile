# =============================================================================
# Backend image — API and worker.
#
# One image, two commands. The API and the Temporal worker share every module
# they import; building them separately would let their dependency sets drift
# and produce the class of bug where a workflow runs correctly in one process
# and fails to deserialize in the other.
#
#   docker build -t ai-receptionist-api backend/
#   docker run --rm ai-receptionist-api                        # API
#   docker run --rm ai-receptionist-api python -m app.worker   # worker
# =============================================================================

# ---- Builder ----------------------------------------------------------------
# uv resolves and installs into a self-contained .venv that is copied into the
# runtime stage, so no build toolchain survives into the final image.
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, in their own layer. Application code changes on every
# commit; the lockfile changes rarely, so this layer stays cached.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- Runtime ----------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Non-root. A container that never needs to write outside /tmp should not be
# able to, and this is the one hardening step that costs nothing.
RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --home /app app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Fail fast rather than emit a partially-written log line on shutdown.
    PYTHONFAULTHANDLER=1

WORKDIR /app

COPY --from=builder --chown=app:app /app /app

USER app

EXPOSE 8000

# No --reload and no --workers. Replica count is the orchestrator's decision
# (compose locally, the ECS service later); a process manager inside the
# container would fight it and hide crashes behind an automatic restart.
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
