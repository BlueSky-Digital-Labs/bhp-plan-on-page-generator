# React/JSON foundation contract

This is the contract every later UI feature builds on. The foundation replaces the
originally proposed server-rendered HTMX interface: **features adapt their described
HTMX behaviour to this React + JSON architecture. They do not add a parallel
HTML-fragment interface.** HTMX is not installed and the backend never returns HTML
fragments.

## Shape

- One origin. FastAPI serves the compiled Vite assets and the API, so the browser only
  makes same-origin requests. There is no CORS configuration and none should be added.
  Locally, Vite's dev server proxies `/api` to FastAPI (`frontend/vite.config.ts`).
- The backend exposes JSON under `/api/*`. Add a router in `backend/app/api/` and
  include it in `backend/app/api/__init__.py`.
- The frontend calls it through `apiRequest<T>()` in `frontend/src/api/client.ts`.
  Add one typed function per endpoint in `frontend/src/api/endpoints.ts`.
- UI state is React's own (`useState`, `useRequest`). Plain CSS in `src/styles.css`.
  Add a router, state manager or component library only for a concrete, stated need.

## Success responses

JSON body, exactly the typed shape the endpoint documents. Mirror it as a TypeScript
interface next to the endpoint function. Every `/api` response carries
`Cache-Control: no-store` and an `X-Correlation-ID` header.

## Error envelope

Every failure from `/api` (including 404, 405, validation and unhandled exceptions) is:

```json
{ "error": { "code": "internal_error", "message": "Something went wrong. Please try again.", "correlation_id": "<32 hex chars>" } }
```

| Rule | Why |
| --- | --- |
| `message` is fixed copy chosen per status, never exception text or `detail`. | Later features handle confidential user text. |
| Validation (422) responses do not echo the submitted input. | FastAPI's default does; it is overridden. |
| `correlation_id` is generated server-side per request; inbound ids are ignored. | Nothing a client sends is reflected back. |
| Logs are JSON with metadata only (`correlation_id`, status, exception class). No tracebacks, bodies or messages. | The reference lets support find the event without content in logs. |

Raise `fastapi.HTTPException` for expected failures. Do not catch-and-format in routes;
the shared handlers in `backend/app/errors.py` do it. Never put user content in `detail`.

On the frontend, failures arrive as `ApiError` (`status`, `code`, `correlationId`). Its
`message` is deliberately **not** the server's. Render failures with `<ErrorNotice>` and
loading with `<Loading>`; they show fixed text plus the correlation reference only. Do
not render `error.message`, response bodies or status text.

## Configuration and secrets

- Backend config is `Settings` in `backend/app/config.py`; secrets are `Secrets` (masked
  `SecretStr`). Missing or invalid required config stops startup with a message naming
  the setting, never its value. `APP_ENV` is required and is not baked into the image.
- **The frontend reads no environment variables.** Anything exposed through `VITE_*` is
  compiled into public browser assets, so it must never hold a secret. If a feature needs
  public build-time config, add it deliberately and keep it non-sensitive.

## Adding a feature (checklist)

1. Backend router + Pydantic response model + pytest using the `client` fixture.
2. Typed function in `src/api/endpoints.ts`; component using `useRequest`.
3. Vitest test for success and failure, asserting no server detail reaches the DOM.
4. Extend `frontend/e2e/` if the feature adds a user-visible flow (runs in Chrome and Edge).
