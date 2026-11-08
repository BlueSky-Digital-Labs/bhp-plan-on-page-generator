# Appendix

The full detail behind the one-page canvas, for assessors: every answer in full, the data
sources and systems, and the whole conversation in order. It runs to as many pages as it needs.
Code: `backend/app/appendix/`, route: `backend/app/api/appendix.py`.

## Download

`GET /api/appendix?format=pdf|docx|json&revision=N` (the caller's own session; no CSRF token, it
changes nothing). `revision` is required and must be the current, **confirmed** revision (see
"Read-back and confirmation" below). The file is built on every request from what the server has
stored and is **never kept**. The file name is the solution name, made safe, plus the kind and
revision, for example `shift-notes-assistant-appendix-r12.pdf`.

| | |
| --- | --- |
| `200` | The file, as an attachment named `plan-on-a-page-appendix.<ext>`, `Cache-Control: no-store`, and `X-Appendix-Revision` (the conversation revision it was built from). |
| `409 canvas_not_ready` | The server's validator does not call the canvas ready (see below), or no interviewer is attached. |
| `409 turn_in_progress` | The assistant has not finished replying, so the draft may be about to change. |
| `409 not_confirmed` | The user has not confirmed this revision. |
| `409 revision_conflict` | `revision` is not the current one (an answer arrived since it was shown), or one arrived while the file was being built. |
| `409 conversation_changed` | An answer arrived while the copy was being taken twice in a row. Try again. |
| `401` | No valid session. Another session's appendix can never be requested. |
| `500 appendix_failed` | The file could not be rendered (logged without content). |

The review screen has an **Appendix** card with a button for each format.

## What is in it

* **Summary**, first, on the opening page(s) after the title:
  * an **overview written by AI** from the whole conversation and nothing else (4 to 8 sentences,
    labelled as AI-written, with a note to check it against Part B). The model is given only the
    conversation and told to add nothing from outside it. Before it is used the server checks
    its length and that **every number in it appears in the conversation**; anything else (a
    failed call, a cut-off answer, an invented number, or a conversation too long to send whole)
    means no overview, and the appendix says so. It is written once per revision, kept only in
    memory, and can never make a download fail. A model cannot be fully prevented from
    paraphrasing wrongly, which is why Part B is always alongside it;
  * **facts and key answers** worked out by plain code: the idea (name and team), priority,
    opportunity types, the start of how it was first described, counts of answers and replies,
    skipped optional questions, and a short line for each answer (cut at about 140 characters,
    marked "…"). These never depend on the model.
* **Part A, full answers**, in the canvas's own order: Overview (name, team, priority), Current
  state, AI opportunity (types and vision), Solution concept, Value and success, **Data sources
  and systems** (their own section, in full), Key assumptions. Optional answers the user skipped
  say so ("Not provided (the user chose to skip this question)"). Nothing is condensed.
* **Part B, conversation record**: every finished message, numbered, labelled User or Assistant,
  in the order sent. Corrections stay in the record; Part A shows the current values. Only the
  stored message text is ever included: no model reasoning, prompts, tool calls or secrets exist
  in the stored turns, and unfinished or failed turns are left out.
* A header with the source revision and turn count, and page numbers on every page.

Formats: **PDF** (A4, selectable text, wraps long words and unbroken strings, nothing cut at a page
break), **Word** (`.docx`, written directly as a zip of XML so no library is needed; page numbers
in the footer), **JSON** (`format`, `version`, `source_revision`, `sections[].answers[]`,
`conversation[]`, plus a `summary` with `overview` (or null), `facts[]` and `key_answers[]`, for other tools).

## Snapshot rules

`AppendixInput` is frozen and built from a copy, so a later change to the draft cannot reach an
export built from it. The conversation revision and the draft are read together; if the revision
moves while reading, it is read again, and after two attempts the request fails rather than mix
two moments. Only a canvas the server calls ready can be exported.

## Read-back and confirmation

Before any document, the review screen reads the whole canvas back ("Check your canvas": every
field, "Not provided" for omitted optional ones, and the priority the user chose) and asks the
user to confirm. Corrections go through the same conversation ("Something is wrong: go back and
correct it"); there is no separate editor.

* `POST /api/canvas/confirm` with `{"revision": N}` (session, same-origin check and CSRF token).
  Only the revision the user was shown, and only while it is the current, complete one. It returns
  the refreshed review (`revision`, `canvas`, `confirmed_revision`, ...). Errors: `422
  canvas_incomplete` (a required field is missing), `409 revision_conflict` (stale),
  `409 turn_in_progress`. The body accepts nothing else, so a priority cannot be supplied here.
* The stamp is the conversation's existing `revision`, and the confirmation is one stored value,
  `confirmed_revision`. It counts only while it equals `revision`. Any later answer moves
  `revision` on, so it ends the confirmation at once; the user must read it back and confirm
  again. No history of old versions is kept.
* **Every document download requires the current, confirmed revision**, checked on the server,
  and checked again just before the file is sent so an answer that arrived while it was built
  cannot let an obsolete canvas through. The page shows the download buttons only for a confirmed
  revision, and re-reads the review straight after any refusal.

## The one-page canvas as PDF or PowerPoint

`GET /api/canvas-file?format=pdf|pptx&revision=N` (`backend/app/canvas_export.py`). The **PDF is
built on the server** (one A4 landscape page, vector text, drawn like the on-screen canvas), and the
PowerPoint is one editable 16:9 slide. Same rules as the appendix: own session, confirmed current
revision only, built per request in memory (no temporary files), never kept, `Cache-Control:
no-store`, attachment with a safe file name. A render failure is a content-free `500` with
guidance on screen and no file; the confirmation and the answers are untouched, so the user can
retry. Text shrinks to fit each panel (down to 5.5 pt); if even that is not enough, the longest
answers are cut with "…" (the whole answer is in the appendix) rather than spill off the page.
The browser no longer builds the PDF.

## When the one-page canvas is offered

The canvas (and the appendix) need `ready_for_review`, computed on the server and never taken
from the browser. It is true only when **all 14 required fields** have a real answer (blank or
spaces do not count), the data and systems confirmations and the priority follow-up have been
done, both optional questions (Business Impact, Expected Value/KPIs) have been asked or answered,
and no question is outstanding. The optional fields may be empty. Each required field is tested
on its own in `tests/interview/test_engine.py`. The **Generate canvas** button, the review page
and the appendix all follow that one verdict.

## Download status and deleting

The canvas and the appendix are tracked separately, and the user may delete their session once
both have been downloaded, in any format, for the current version; see [downloads.md](downloads.md).

## Sending by email

The confirmed canvas and this appendix can also be emailed as two PDFs from the review page; see
[email.md](email.md). It uses the same confirmation gate as the downloads.

## Limits

* The canvas PDF and the appendix PDF use DejaVu Sans (`app/appendix/fonts/`, licence alongside): Latin, Greek and
  Cyrillic and most punctuation are covered; scripts it lacks (for example Chinese) and emoji
  cannot be drawn in the PDF. The Word and JSON files keep every character.
* The Word file was checked by parsing its XML and by opening it with macOS `textutil`, not in
  Microsoft Word itself.
* Public delivery beyond this download, amendments by revision (feature 15) and a permanent
  appendix store are not built. The appendix is built from the stored conversation, which is in
  DynamoDB when deployed.
