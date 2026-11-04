# Appendix

The full detail behind the one-page canvas, for assessors: every answer in full, the data
sources and systems, and the whole conversation in order. It runs to as many pages as it needs.
Code: `backend/app/appendix/`, route: `backend/app/api/appendix.py`.

## Download

`GET /api/appendix?format=pdf|docx|json` (the caller's own session; no CSRF token, it changes
nothing). The file is built on every request from what the server has stored and is **never kept**.

| | |
| --- | --- |
| `200` | The file, as an attachment named `plan-on-a-page-appendix.<ext>`, `Cache-Control: no-store`, and `X-Appendix-Revision` (the conversation revision it was built from). |
| `409 canvas_not_ready` | The server's validator does not call the canvas ready (see below), or no interviewer is attached. |
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

## The one-page canvas as PowerPoint

The review screen also offers **Download canvas (PowerPoint)**: `GET /api/canvas-file?format=pptx`
(`backend/app/canvas_export.py`) returns one 16:9 slide laid out and coloured like the on-screen
canvas, as native editable shapes and text. It follows the same rules as the appendix: the
caller's own session, only a canvas the server calls ready (`409 canvas_not_ready` otherwise),
built per request and never kept. Text shrinks (down to 6 pt) to fit each panel; the slide was
previewed with macOS Quick Look, not in PowerPoint itself, so open it once before relying on it.
The PDF button is unchanged and still builds in the browser.

## When the one-page canvas is offered

The canvas (and the appendix) need `ready_for_review`, computed on the server and never taken
from the browser. It is true only when **all 14 required fields** have a real answer (blank or
spaces do not count), the data and systems confirmations and the priority follow-up have been
done, both optional questions (Business Impact, Expected Value/KPIs) have been asked or answered,
and no question is outstanding. The optional fields may be empty. Each required field is tested
on its own in `tests/interview/test_engine.py`. The **Generate canvas** button, the review page
and the appendix all follow that one verdict.

## Limits

* The PDF font is DejaVu Sans (`app/appendix/fonts/`, licence alongside): Latin, Greek and
  Cyrillic and most punctuation are covered; scripts it lacks (for example Chinese) and emoji
  cannot be drawn in the PDF. The Word and JSON files keep every character.
* The Word file was checked by parsing its XML and by opening it with macOS `textutil`, not in
  Microsoft Word itself.
* Public delivery beyond this download, amendments by revision (feature 15) and a permanent
  appendix store are not built. Like the conversation, it needs the durable store before it works
  when deployed (the deployed conversation store fails closed).
