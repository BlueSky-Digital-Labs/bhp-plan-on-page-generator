# Access code and private canvas sessions

ROC users enter a shared access code, then start a private canvas session. There is no BHP
single sign-on, no user accounts and no admin portal. June distributes and rotates the
code. Code: `backend/app/access/`, routes: `backend/app/api/access.py`.

## Flow

Browser routes (served by the same React app; the API decides who may see what):
`/` access code, `/start` start screen, `/conversation` where a started session continues.

| Step | Request | Result |
| --- | --- | --- |
| 1 | `GET /api/access-state` | `{"start_permitted": true\|false}`. Always `200`, a boolean only: true for a valid, unexpired grant **or** an existing valid session. The cookies are HttpOnly, so the page asks the server instead of reading them. |
| 2 | `GET /api/csrf` | Returns `{csrf_token}` and sets the `__Host-csrf` cookie. |
| 3 | `POST /api/access` (form field `access_code`, header `X-CSRF-Token`) | Valid: `200` and a signed **grant** cookie (10 min). Invalid: `401 invalid_access_code`, nothing issued. |
| 4 | `POST /api/sessions` (header `X-CSRF-Token`, no body) | Creates a **new** independent record. Authority is a grant (consumed once) or an already-authorised session. Replaces the grant with a signed **session** cookie. `201` with `{created_at, expires_at, next}`; `next` is the server-chosen route (`/conversation`). |
| 5 | `GET /api/session` / `DELETE /api/session` | Read or end the caller's own session. |

The originally described HTMX responses and redirects are JSON plus `Set-Cookie`, as the
React/JSON contract requires; the SPA decides where to navigate.

## Security properties

* **Cookies** are `__Host-` prefixed (the browser enforces `Secure`, `Path=/`, no `Domain`),
  `HttpOnly`, `SameSite=Lax`, with bounded `Max-Age`. Payload is only
  `{purpose, opaque id, expiry}`: never the access code or idea content.
* **Signed with HMAC-SHA256**, with a separate key derived per purpose, so a grant can never
  be presented as a session. Tampered, malformed, expired and wrong-purpose cookies are
  rejected.
* **Server-side authorisation.** Every session read goes through one check
  (`AccessService.load_session`): signature, purpose, cookie expiry, then the session record
  must exist and its own `expires_at` must be in the future. The record is authoritative.
  All failures return the same `401 unauthenticated`, so a client cannot tell whether a
  session exists.
* **No client-chosen identity.** Session ids are server-generated (256-bit random). Nothing
  in a query string, header or body can select another session; endpoints only ever act on
  the session in the caller's own signed cookie.
* **Fixed 24 h expiry**, set at creation and never extended by activity. Starting another
  canvas (with a fresh grant, or from an existing session) creates a new independent record and
  never touches existing records, which keep their original expiry. A grant that is presented
  but already used is rejected, never silently replaced by the session.
* **Grants are single use**, claimed with a conditional write, so a replayed grant cookie
  cannot create further sessions.
* **CSRF / cross-site:** every state-changing route requires a same-origin `Origin`
  (or `Sec-Fetch-Site: same-origin`) **and** a double-submit token (`X-CSRF-Token` header
  equal to the `__Host-csrf` cookie). A request proving neither is rejected. `SameSite=Lax`
  is defence in depth.
* **Fail closed.** If Secrets Manager or DynamoDB cannot be used, or the stored hash is
  malformed, the response is `503 service_unavailable` with `Retry-After`. No cached
  plaintext code is ever accepted and no untracked session is created.
* **Logs** carry an event name, a safe reason category and the correlation id. They never
  contain the submitted code, form bodies, cookies, hashes or keys.
* The access-code **hash is fetched on every verification** (not cached), so rotation takes
  effect on the next attempt without a restart.

## Configuration

Non-secret identifiers, all **required when `APP_ENV=deployed`** (startup fails naming any
that are missing):

| Variable | Meaning |
| --- | --- |
| `SESSIONS_TABLE_NAME` | DynamoDB table for sessions and grants. |
| `ACCESS_CODE_HASH_SECRET_ID` | Secrets Manager secret holding the Argon2 hash (plain `SecretString`). |
| `COOKIE_SIGNING_KEY_SECRET_ID` | Secrets Manager secret holding the cookie-signing key (`SecretString`, at least 32 characters). |

Secret **values** are never configuration: they live in Secrets Manager (or, with
`SECRETS_BACKEND=parameterstore`, as SecureString parameters in Systems Manager Parameter Store) and are
read through the workload role.

### DynamoDB table

Partition key `session_id` (String) and sort key `sk` (String): the same table also holds the
conversation content (layout in `backend/app/dynamo.py`; `infra/` creates it). **Enable TTL on the attribute `expires_at`** (epoch
seconds). TTL deletion is lazy (up to about 48 h later), so the application checks
`expires_at` itself, and the scheduled sweep ([expiry.md](expiry.md)) removes sessions at 24 h.
Add the sparse global index `expiry-index` (partition `gsi_pk`, sort `expires_at`, keys only). Encrypt
with the customer-managed KMS key.

### Workload role permissions (add to the task role; nothing broader)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": [
        "arn:aws:secretsmanager:ap-southeast-2:<account-id>:secret:<access-code-hash-secret>-*",
        "arn:aws:secretsmanager:ap-southeast-2:<account-id>:secret:<cookie-signing-key-secret>-*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:BatchWriteItem",
        "dynamodb:ConditionCheckItem",
        "dynamodb:Query"
      ],
      "Resource": [
        "arn:aws:dynamodb:ap-southeast-2:<account-id>:table/<sessions-table>",
        "arn:aws:dynamodb:ap-southeast-2:<account-id>:table/<sessions-table>/index/expiry-index"
      ]
    }
  ]
}
```

If the secrets or table use a customer-managed KMS key, also allow `kms:Decrypt` (and
`kms:GenerateDataKey` for the table) on that key. The Secrets Manager, DynamoDB and
(for the AI feature) Bedrock VPC endpoints must be reachable from the private subnets.

### Brute-force protection (release dependency)

Rate limiting is the runtime's job, not the app's: add an **AWS WAF rate-based rule scoped
to `POST /api/access`** (for example a small number of requests per source IP per 5
minutes, blocking on breach). The shared code is guessable by volume otherwise. The app
bounds the cost of each attempt (code length cap, Argon2) but does not count attempts.
Confirm the rule exists before release.

## Rotating the access code (runbook)

**Who does what.** June chooses and distributes the code. June never gets AWS credentials
and never touches the application. An authorised AWS operator (a named Horizon Digital
engineer with Secrets Manager write access) applies the change. There is no admin portal.

1. **June chooses the new code** and creates its hash on their own machine (nothing leaves
   it except the hash, which is not the code):
   ```bash
   cd backend && uv run python -c "import getpass; from argon2 import PasswordHasher; print(PasswordHasher().hash(getpass.getpass('New access code: ')))"
   ```
   June sends the printed `$argon2id$...` line to the operator over the agreed channel.
   **Never send the code itself** to the operator, and never paste either into a ticket.
2. **The operator stores it** (file input keeps it out of shell history):
   ```bash
   printf '%s' '<hash line from June>' > /tmp/hash.txt
   aws secretsmanager put-secret-value --region ap-southeast-2 \
     --secret-id <ACCESS_CODE_HASH_SECRET_ID> --secret-string file:///tmp/hash.txt
   shred -u /tmp/hash.txt 2>/dev/null || rm -P /tmp/hash.txt
   ```
3. **Verify** (no restart needed; it applies to the next attempt): the operator or June
   confirms the **old code is refused** and the **new code is accepted** on the entry page.
4. **June distributes the new code** to ROC users through the usual channel.

**Effect on open sessions.** Rotation only prevents *new entry* with the old code. Sessions
already started keep working until their own 24 h expiry or until deleted. Starting
another canvas needs the new code.

**Emergency: end all sessions now.** Rotating `COOKIE_SIGNING_KEY_SECRET_ID` makes every
existing cookie invalid (each task picks up the new key within about 60 seconds). Deleting
individual items from the sessions table removes specific sessions.

**First-time setup.** Create the key with `openssl rand -base64 48`, store it with
`aws secretsmanager create-secret` (or `put-secret-value`), and the hash as in step 1-2.

## Frontend

Three routes, a small history-based hook (`src/router.ts`) and no routing library:

* `/`: asks `GET /api/access-state`; a browser that already has access goes straight to
  `/start`, otherwise it sees the access form (`AccessGate`). A correct code goes to `/start`.
* `/start`: shows only a loading state until the backend has answered, so protected content
  never flashes. Without access it returns to `/` with a neutral "your access has expired or
  has not been entered" note. With access it shows the explainer (journey, saving both
  documents yourself, nothing is submitted to a backlog, temporary Australian-hosted
  processing, purge after both downloads, automatic deletion after 24 hours, and no claim that
  data-handling approval has been granted) and the single **Start a new canvas** action. The
  action is disabled while pending, can be retried after a failure, and a `401` sends the user
  back to entry.
* `/conversation`: the route the conversation feature will fill. It requires a real session
  (checked by the backend) and for now shows the canvas workspace. **Navigation contract:** the
  server's `next` value decides where to go; the client follows only a plain in-app path
  (`safeRoute`) and otherwise falls back to `/conversation`, so a bad response can never
  redirect off-site.

Direct navigation or refresh on `/start` or `/conversation` works because the backend serves
the app for extension-less non-API paths (`SpaStaticFiles`); `/api/*` and missing files still
return the JSON error envelope. Protection never depends on which HTML is served. All
messages are fixed copy keyed on the error code; nothing from a response body is rendered, the
code field is cleared after every submit, and nothing is written to localStorage or
sessionStorage.

## Local development

Run the two servers and open **http://localhost:5173**. With no table or secret ids
configured the backend uses an **in-memory** store (sessions are lost on restart) and reads
the secrets from the environment (local/test only; deployed always uses Secrets Manager and
DynamoDB):

```bash
# terminal 1 (prompts for a code of your choice; nothing is printed)
cd backend
export ACCESS_CODE_HASH="$(uv run python -c "import getpass; from argon2 import PasswordHasher; print(PasswordHasher().hash(getpass.getpass('Access code: ')))")"
export COOKIE_SIGNING_KEY="$(openssl rand -base64 48)"
APP_ENV=local uv run uvicorn --factory app.main:create_app --port 8000

# terminal 2
cd frontend && npm run dev
```

Cookies are always `Secure`; Chrome and Edge accept them on `localhost`. **Safari does not**
(the CSRF cookie is never stored, so sign-in fails with "Something went wrong"): use Chrome or
Edge locally. Over HTTPS in AWS, Safari is fine. The Vite dev proxy
must keep `changeOrigin: false` (it is set in `vite.config.ts`): the backend rejects
requests whose `Origin` doesn't match the `Host` it receives, and the default proxy setting
rewrites `Host`. To try a second visitor, open a private window.

## Not in scope

BHP SSO, user accounts, an admin portal or any UI for managing secrets, conversation
content, the entry-screen presentation, and the scheduled expiry worker.
