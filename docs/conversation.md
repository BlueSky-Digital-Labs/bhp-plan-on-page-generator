# Conversation screen

Users describe an idea in their own words at `/conversation` and see the conversation in
order. Replies appear progressively, and an interrupted submission can be recovered without
re-entering an answer that is already recorded. Code: `backend/app/conversation/`,
`backend/app/api/conversation.py`, `frontend/src/components/ConversationScreen.tsx`.

This ticket supplies the **presentation surface and a minimal turn contract**. It does not
contain interview logic, completeness decisions, priority selection, review or PDF controls.

## Browser routes

| Route | Shows |
| --- | --- |
| `/conversation` | The conversation screen. Needs a real session (checked by the backend). |
| `/canvas` | The original template form and status panel, kept reachable but not linked. |

Direct navigation and refresh load the app (the backend serves it for client-side routes);
nothing is shown until the backend has confirmed the session.

## JSON API (all under `/api`, cookie-authorised, `Cache-Control: no-store`)

| Request | Result |
| --- | --- |
| `GET /api/conversation` | `{revision, turns: [{turn_id, role, text, status}]}` in order. The stored history is the source of truth. `status` is `complete`, `pending` or `failed`. |
| `POST /api/conversation/turns` `{submission_id, expected_revision, text}` (CSRF + same-origin) | `201 {revision, user_turn, assistant_turn}` (assistant turn `pending`). The same `submission_id` again returns `200` with the recorded turns and creates nothing. `409 revision_conflict` if the revision is stale, `409 turn_in_progress` if the last reply is unfinished, `409 conversation_full`. Text is 1-4000 characters. |
| `GET /api/conversation/turns/{turn_id}/stream` | `application/x-ndjson`, one JSON object per line: `{"type":"delta","text":"..."}` zero or more times, then exactly one `{"type":"complete"}` or `{"type":"failed","reason":"unavailable"\|"processing_error"}`. |

The stream exposes no prompts, tool definitions, model identifiers or internal errors, only
text fragments and a terminal state.

### Semantics

* **Streamed text is transient.** The reply is committed to the stored turn only when the
  producer finishes. A dropped connection commits nothing and leaves the turn `pending`; the
  client reloads the stored state and may stream it again from the start.
* **A completed turn is never regenerated.** Streaming it again replays the stored text.
* **Retrying is safe.** The browser keeps one `submission_id` per answer across retries (a new
  one only if the text is edited), so an answer cannot be recorded twice.
* **Ownership.** Every turn is scoped to the caller's session. An unknown id, a malformed id,
  another session's turn and the caller's own user turn all return the same `404`.
* Ending the session (or expiry) removes access to the conversation immediately; ending it
  also deletes the stored turns.

## Where replies come from

The guided interviewer is documented in [interview.md](interview.md). The producers below are the seam it plugs into.

`TurnProducer` is the seam for the real interviewer (feature 11). The default
(`CONVERSATION_PRODUCER=unavailable`) **never fabricates a reply**: the stream ends with
`failed / unavailable` and the screen offers a retry. `CONVERSATION_PRODUCER=test` enables a
scripted producer (fixed text, delivered a word at a time) for presentation tests and local
demos. It is **refused when `APP_ENV=deployed`**.

## Not production-ready yet (release dependencies)

* **Durable storage.** Turns are held in process memory locally and in tests. Deployed, they are in
  the one DynamoDB table (`DynamoConversationStore`, see [downloads.md](downloads.md)); it has been
  tested against a simulated DynamoDB, not yet against AWS.
* **A real producer.** Until feature 11 attaches the interviewer, deployed replies are
  `failed / unavailable`.

## Safety properties

* User and model text is rendered as React text, never as HTML (no `dangerouslySetInnerHTML`),
  and is tested with markup in both user and streamed model content.
* Nothing is written to localStorage, sessionStorage, IndexedDB or a service-worker cache
  (browser-tested). Streamed fragments live only in React state.
* A new canvas is a new session with an empty conversation.
* Errors use fixed copy (plus the correlation reference); response bodies are never rendered.
* Stream and history responses carry `Cache-Control: no-store`; no CORS, no bearer tokens.

## Try it locally

```bash
# backend (see access-control.md for the code/key variables)
CONVERSATION_PRODUCER=test APP_ENV=local uv run uvicorn --factory app.main:create_app --port 8000
# frontend
npm run dev     # then sign in at http://localhost:5173 and start a canvas
```
