# Download status and deleting a session

Users save their own documents outside the app; downloading does **not** submit anything to a backlog.
The server records when it has finished sending each of the two documents (the one-page canvas and the
appendix, in any format), shows that status, and lets the user delete their session only after both
have been sent for the **current** version.

Code: `backend/app/delivery/` (records and the gate), `backend/app/api/downloads.py` (status),
`backend/app/api/access.py` (`POST /api/session/purge`), `backend/app/conversation/service.py`
(`record_delivery`, `download_gate`, `purge`), `frontend/src/components/DownloadStatus.tsx`.

## What "downloaded" means

Each document is sent as a streamed response. The server notes "delivered"
only **after the last chunk has been handed over**. It does not note it on arrival of the request,
when headers go out, if the connection drops or is cancelled, or if building the file failed.

That is the only fact recorded. It does **not** prove the browser kept the file or that the user
saved it, and the screen says so. A document counts in whatever format it was sent: the canvas as PDF or PowerPoint, the appendix as PDF,
Word or JSON (`delivery_recorder` in `app/api/exports.py`). One format of each is enough. The email does
not count.

## The gate

`evaluate_gate` (`app/delivery/gate.py`) is the one rule, used both to show the status and again
immediately before a deletion is accepted. Both documents count only if they were sent for the
**current** revision, so an amendment resets both at once, and a completion that arrives late for an
older revision can never unlock anything (its revision no longer matches). Eligibility also needs at
least one answer to be in and the assistant's reply to be finished. It does **not** need the canvas to
be complete or confirmed: someone who cannot finish can save what they have and **stop** the session
(see "Saving a draft and stopping" below). A finished canvas is simply the case where `complete` is true.

`GET /api/downloads` (own session only, `Cache-Control: no-store`):

```json
{"revision": 3, "canvas_downloaded": true, "appendix_downloaded": false,
 "purge_eligible": false, "lock_reason": "appendix_not_downloaded", "complete": true}
```

`lock_reason` is one of `not_ready` (nothing answered yet, so nothing to save), `turn_in_progress`,
`canvas_not_downloaded`, `appendix_not_downloaded`, `both_not_downloaded`, or null when eligible.
`complete` is true only when every required answer is in and the user confirmed this revision; it only
chooses the wording on screen ("delete" or "stop").

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

## Saving a draft and stopping

Downloads no longer need the canvas to be finished or confirmed: the server serves the **current
revision** as long as one answer is in and the assistant has finished replying
(`downloadable_snapshot` in `app/conversation/service.py`). An unfinished canvas is a **draft**:

* the files are named `...-canvas-draft-r<N>` and `...-appendix-draft-r<N>`;
* the appendix says "UNFINISHED DRAFT" under its title (PDF, Word) or has `"draft": true` (JSON); the
  one-page canvas keeps its design and shows "Not provided" for what is missing;
* **emailing still needs a complete, confirmed canvas.** The Submit by email button is on the review
  page only, and the route refuses an unfinished one (`canvas_not_ready`).

On the conversation screen, once an answer is in and the canvas is not yet complete, a **Save your
progress** card offers *Download canvas so far* and *Download appendix so far*. When the server says
both were saved for the current revision, **Stop session** appears; after a confirmation it calls the
same `POST /api/session/purge`, so the server deletes the conversation, draft and records and ends the
sign-in, exactly as for a finished canvas. The entry page then explains how to carry on. Answering
again after saving makes a new revision, so both documents must be saved again before stopping.

**Carrying on later** uses the existing attach-a-file feature ([attachments.md](attachments.md)): in a
new session the user attaches the downloaded canvas PDF or the appendix to their first answer, and the
assistant reads it as if typed and asks for what is still missing. Nothing is stored for them
meanwhile. The one-page canvas is the better file to attach: a long appendix can run past the 15,000
character limit on attached text, which is cut at a word.

## On screen

On the review page, once the canvas is confirmed, a **Your documents** card (on the conversation screen, the **Save your progress** card, with its own wording) shows each document as
"Downloaded" or "Not downloaded yet" (words and symbols, not colour alone), a locked explanation
that names what is outstanding, guidance to save both documents, and a **Refresh status** button. The
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
