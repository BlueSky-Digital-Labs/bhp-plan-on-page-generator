# Download status and deleting a session

Users save their own PDFs outside the app; downloading does **not** submit anything to a backlog.
The server records when it has finished sending each of the two PDFs, shows that status, and lets
the user delete their session only after both PDFs of the **current** version have been sent.

Code: `backend/app/delivery/` (records and the gate), `backend/app/api/downloads.py` (status),
`backend/app/api/access.py` (`POST /api/session/purge`), `backend/app/conversation/service.py`
(`record_delivery`, `download_gate`, `purge`), `frontend/src/components/DownloadStatus.tsx`.

## What "downloaded" means

The canvas PDF and the appendix PDF are sent as a streamed response. The server notes "delivered"
only **after the last chunk has been handed over**. It does not note it on arrival of the request,
when headers go out, if the connection drops or is cancelled, or if building the file failed.

That is the only fact recorded. It does **not** prove the browser kept the file or that the user
saved it, and the screen says so. Only the PDF of each document counts (`COUNTED_FORMAT` in
`app/api/exports.py`); the PowerPoint, Word and JSON files and the email do not.

## The gate

`evaluate_gate` (`app/delivery/gate.py`) is the one rule, used both to show the status and again
immediately before a deletion is accepted. Both documents count only if they were sent for the
**current** revision, so an amendment resets both at once, and a completion that arrives late for an
older revision can never unlock anything (its revision no longer matches). Eligibility also needs the
canvas to be complete, the assistant's reply finished, and the current revision confirmed.

`GET /api/downloads` (own session only, `Cache-Control: no-store`):

```json
{"revision": 3, "canvas_downloaded": true, "appendix_downloaded": false,
 "purge_eligible": false, "lock_reason": "appendix_not_downloaded"}
```

`lock_reason` is one of `not_ready`, `turn_in_progress`, `not_confirmed`, `canvas_not_downloaded`,
`appendix_not_downloaded`, `both_not_downloaded`, or null when eligible.

## Deleting

`POST /api/session/purge` with `{"revision": 3}` (session cookie and CSRF token). The body can hold
nothing else: eligibility is never taken from the browser. The server re-evaluates the gate, then
deletes only if no answer has arrived since (one conditional step), removes the conversation, canvas
draft, delivery records and cached overview, ends the sign-in session and clears the cookie (204).

| Status | `error.code` | When |
| --- | --- | --- |
| 409 | `purge_locked` | The gate is not satisfied. Nothing is deleted or changed. |
| 409 | `revision_conflict` | The revision is not the current one, or an answer arrived meanwhile. Nothing is deleted. |
| 503 | `service_unavailable` | A store could not be read. Nothing is deleted. |

Signing out (`DELETE /api/session`) is unchanged: it still deletes everything at once, with no
download check. Whether sign-out should keep doing that is a product decision (see below).

## On screen

On the review page, once the canvas is confirmed, a **Your documents** card shows each PDF as
"Downloaded" or "Not downloaded yet" (words and symbols, not colour alone), a locked explanation
that names what is outstanding, guidance to save both PDFs, and a **Refresh status** button. The
**Delete my session** button appears only when the server says both are done, and asks for one more
confirmation. The page:

* reads the status on arrival, after each download attempt, and twice more (1.5 s and 4 s later)
  because a file can finish just after the first read; then it stops, leaving the manual button;
* hides the delete action while a read is in flight and drops it if a read fails (no stale
  eligibility), keeps only the newest read's answer, and re-reads the canvas if the status is for a
  different revision (another tab answered);
* returns to entry if the session ended, and shows fixed text for every failure.

## Where the records live, and moving to DynamoDB

Everything above runs on in-memory stores locally and in tests; deployed runs still fail closed
because there is no durable store (`UnavailableConversationStore`, `UnavailableDeliveryStore`).
The stores were shaped so that a DynamoDB version is an implementation change, not a redesign:

| Store | Operations (the whole contract) |
| --- | --- |
| Delivery (`DeliveryStore`) | `record` (idempotent, never goes to an older revision), `get`, `delete_session` |
| Conversation | `history`, `submit` (revision-checked), `get_turn`, `complete_turn`, `mark`, `delete_session`, `delete_session_if_revision`, `expired_session_ids(now, limit, after)` |
| Interview state | `get`, `put`, `confirm`, `delete_session` |

Suggested table layout (one table, partition key `session_id`, TTL attribute `expires_at`):

* `META` item: revision, confirmed revision, interview state, `expires_at`. Turns are **separate
  items** (`TURN#0001`, …) so a long conversation with attached files never meets the 400 KB item
  limit. Delivery records are items `DELIVERY#canvas` and `DELIVERY#appendix`, written with a
  condition "attribute missing or revision less than this one" (that is `record`).
* Every read that feeds the gate is a **strongly consistent** read.
* `delete_session_if_revision` becomes a conditional update on `META` (revision equals the value
  shown) that also sets a `purging` flag; every write (`submit`, `complete_turn`) is conditional on
  that flag being absent, so no content is written once deleting has begun. The items are then
  deleted in batches, and the whole step is safe to repeat if it is interrupted.
* `expired_session_ids` reads a sparse index (a GSI on `META` items only, keyed by a constant and
  sorted by `expires_at`) that holds ids and times, never content. That index is also what lets the
  expiry sweep find sessions even after TTL has removed a primary record.
* Items expire with their session (`expires_at` is fixed at creation, 24 hours); TTL is lazy
  (it can lag by up to about two days), so the application checks `expires_at` itself.

## Limits

* "Downloaded" is a server-side delivery boundary only. See above.
* Sign-out deletes immediately and is not gated.
* A user who never completes the canvas, or never downloads both files, cannot delete manually:
  their session is removed by the 24-hour expiry ([expiry.md](expiry.md)).
* Not run against real DynamoDB or on more than one server. The in-memory stores are per process.
