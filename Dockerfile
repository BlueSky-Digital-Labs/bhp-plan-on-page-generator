# syntax=docker/dockerfile:1

# --- Stage 1: compile the Vite frontend (public assets only; no secrets at build time) ---
FROM node:24-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: FastAPI runtime serving the API and the compiled assets on one origin ---
FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

# uv resolves nothing at build time: --frozen installs exactly what uv.lock pins.
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev

COPY backend/app ./app
# Used only when the deployment sets AWS_CONFIG_FILE and BEDROCK_PROFILE (infra bedrock_role_arn).
COPY backend/aws-config ./aws-config
COPY --from=frontend-build /build/dist ./static

# Only the asset location is baked in. APP_ENV is intentionally NOT set here: the
# deployment must provide it, and the container refuses to start without it.
ENV STATIC_DIR=/app/static

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"]

CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
