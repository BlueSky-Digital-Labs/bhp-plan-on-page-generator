# Plan on a Page

Browser application for roughly 50 WAIO ROC users to complete the Plan on a Page journey
(Edge and Chrome). This repo is the application foundation: a React + TypeScript + Vite
frontend and a FastAPI backend, packaged as one container for AWS ECS Fargate (Sydney).
Feature work builds on the [React/JSON contract](docs/frontend-contract.md).

```
backend/    FastAPI app (app/), pytest tests, uv project (pyproject.toml + uv.lock)
frontend/   Vite + React + TypeScript, Vitest tests, Playwright smoke checks (e2e/)
Dockerfile  Multi-stage build, non-root runtime, same-origin serving
docs/       frontend-contract.md
```

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.13 itself) and Node 22+
(24 recommended). No credentials are needed.

## Run locally

```bash
# terminal 1: FastAPI on :8000
cd backend
uv sync --frozen
APP_ENV=local uv run uvicorn --factory app.main:create_app --reload --port 8000

# terminal 2: Vite on :5173, proxying /api to :8000
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. API docs are at http://localhost:8000/api/docs when `APP_ENV`
is not `deployed`. To add a backend dependency: `uv add <pkg>` (updates `uv.lock`).

## Checks

```bash
# backend
cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest

# frontend
cd frontend && npm run typecheck && npm test && npm run build

# Edge + Chrome smoke checks, against the built app served by FastAPI on one origin
# (run the frontend build first; needs Chrome and Edge: npx playwright install chrome msedge)
cd frontend && npx playwright test
```

CI runs all of this, plus the container check, in `.github/workflows/ci.yml`.

## Run the container

```bash
docker build -t plan-on-a-page .
docker run --rm -p 8000:8000 -e APP_ENV=local plan-on-a-page
# http://localhost:8000  (health: /api/health)
```

The container exits at startup if `APP_ENV` is missing. Behind the ALB, point the target
group health check at `/api/health`.

## Configuration

| Variable | Required | Notes |
| --- | --- | --- |
| `APP_ENV` | yes | `local`, `test` or `deployed`. `deployed` also requires the compiled frontend and hides API docs. |
| `AWS_REGION` | no | Default `ap-southeast-2`. |
| `LOG_LEVEL` | no | Default `INFO`. JSON logs, metadata only. |
| `STATIC_DIR` | no | Compiled assets; set to `/app/static` in the image. |
| `ACCESS_CODE_HASH`, `COOKIE_SIGNING_KEY` | later | Secrets; supplied from Secrets Manager, never committed or built into assets. |

The frontend has no environment configuration. See the contract for why.
