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

## Where the records live

Locally and in tests everything is in this process's memory. **Deployed, everything is in one
DynamoDB table** (`SESSIONS_TABLE_NAME`), so any task can serve any request and a delete, purge or
expiry removes it all. The table layout (keys, item types, the sparse expiry index, TTL) is documented
in `backend/app/dynamo.py`, and `infra/` creates exactly that table; the tests build it from the same
definition, so the code and the table cannot drift apart unnoticed.

| Store | In memory | DynamoDB | What it holds |
| --- | --- | --- | --- |
| Sign-in (`SessionStore`) | `InMemorySessionStore` | `DynamoSessionStore` | `ACCESS` and `GRANT` items |
| Conversation | `InMemoryConversationStore` | `DynamoConversationStore` | one `CONV` item, one `TURN#nnnn` item per turn |
| Interview state | `InMemoryInterviewStore` | `DynamoInterviewStore` | one `INTERVIEW` item (the draft as JSON) |
| Delivery | `InMemoryDeliveryStore` | `DynamoDeliveryStore` | `DELIVERY#canvas`, `DELIVERY#appendix` |

How the DynamoDB versions keep the in-memory behaviour:

* An answer is **one transaction** (the `CONV` item's revision plus both new turns), conditional on
  the revision the user was shown, no reply still open and no deletion started. Two tasks answering
  at once cannot both win, and a retried answer (same submission id) is recorded once.
* A reply is committed in one transaction (turn complete, open turn cleared), set-once.
* `delete_session_if_revision` first sets a `purging` flag on `CONV` in one conditional step (only if
  the revision is unchanged), after which no answer or reply can be written; the items are then
  deleted in batches. If that is interrupted, asking again finishes it.
* A delivery record is one conditional write ("nothing held, or this revision is newer").
* Every read that feeds the gate is **strongly consistent**; turns are separate items so a long
  conversation with attached files never meets the 400 KB item limit.
* `expired_session_ids` reads the sparse `expiry-index` (only `CONV` items are in it: ids and times,
  never content), so the sweep still finds a session whose sign-in record TTL has already removed.
* A storage failure is always one fixed error (503, nothing changed); a record that does not parse
  (a corrupt draft) fails closed and is never replaced by a blank one.

The same behaviour checks run against both: `tests/dynamo/test_stores_contract.py` runs one set of
store checks on each, and the whole download-status/purge suite, the interview journey and the
confirmation tests are run once on memory and once on DynamoDB (`store_kind` in `tests/conftest.py`).
DynamoDB itself is simulated with `moto`.

## Limits

* "Downloaded" is a server-side delivery boundary only. See above.
* Sign-out deletes immediately and is not gated.
* A user who never completes the canvas, or never downloads both files, cannot delete manually:
  their session is removed by the 24-hour expiry ([expiry.md](expiry.md)).
* **Not run against real DynamoDB.** The DynamoDB stores are tested against `moto`, which implements
  transactions and conditions faithfully but is not AWS. Run [the canary scan](deployment.md) once
  against the real table.
* The interview draft is saved as one item with last-write-wins, as in memory. Confirming and a
  reply finishing at the same instant could overwrite each other; confirming is only allowed once
  the reply has finished, so this needs two tabs acting within milliseconds.
* The cached appendix overview and the per-session email count are per task (not shared), so with
  several tasks an overview may be written twice and the 5-email cap is per task.
