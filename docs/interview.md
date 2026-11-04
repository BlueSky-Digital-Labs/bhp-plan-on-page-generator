# Guided interview

Turns what a user says into canvas fields and asks for what is missing, **one question per
turn**. Code: `backend/app/interview/`. It plugs into the conversation stream
([conversation.md](conversation.md)) and adds no new endpoint: `GET /api/conversation` now also
returns the draft as `canvas: {fields, missing_required, ready_for_review}` and the interview's
position as `progression` (below), and the conversation screen shows the draft beside the
transcript.

## How a turn works

1. The user's answer is read by an **extractor** (the only part that reads free text). It can
   only *propose* field updates `{field, value, source_turn_ids}`.
2. The **engine** (`engine.py`, deterministic) checks every proposal, applies it to a *copy* of
   the interview state, and chooses the next question itself from the validator's gaps.
3. The reply (a brief acknowledgement plus exactly one question) streams to the user. Only when
   the assistant turn is **stored** does the new state commit (draft fields, prompt markers,
   next target). A dropped connection or failed turn changes nothing, so a retry or refresh
   asks for the same thing and never applies an answer twice.

The model never decides completeness, the next target, priority, or what the user sees if its
wording is invalid.

## Progression (server-owned)

`GET /api/conversation` returns, beside `canvas`:

```json
"progression": {
  "phase": "interview | clarification | data_and_systems | review_ready",
  "outstanding_question": {"target": "painPoints", "kind": "missing", "text": "…"} | null,
  "clarification": {"active": false, "reason": "conflict | unsure | unclear | null"},
  "validation": {"missing_required": ["…"], "unsure": ["…"]},
  "data_and_systems_complete": false,
  "optional_prompted": ["businessImpact"],
  "optional_declined": []
}
```

It is computed from the stored interview state, so it survives a refresh and agrees with the
last assistant turn (that turn ends with `outstanding_question.text`). The outstanding question's
target is stored with the draft, so a failed turn, a retry or a reload asks for the same thing.
`data_and_systems_complete` is set only by the two dedicated confirmation turns, never by the
data or systems being mentioned in passing, so an initial description cannot skip that pass.
`review_ready` and `ready_for_review` are both computed by the server; the browser sends only
the answer text. The conversation screen shows the phase as a small label (fixed copy keyed on
the enum).

## Rules the engine enforces (each has a test)

* Only the 16 canvas ids (same as `frontend/src/canvas/fields.ts`); only the six opportunity
  types; priority only Low/Medium/High/Strategic. Unsupported fields or values are **rejected,
  never coerced**; one bad opportunity type rejects the whole update.
* **Priority is only ever the user's explicit choice**: accepted only while the question being
  answered is the priority question *and* the user's message names that priority.
* Every fact must cite a real user turn id.
* A different answer to an already-answered field is not applied: the interviewer asks which to
  keep (`Earlier you said “…”, but now I heard “…”. Which one should I use?`).
* "I don't know" / "skip" is read deterministically from short replies. For a required field it
  is recorded as uncertainty (never satisfies the validator), the question is rephrased once, then
  the field is revisited after the others. Optional fields are marked declined and never block.
* After all required fields: a **dedicated confirmation turn for data, then another for
  systems**, even if the user already mentioned them; then Business Impact and Expected
  Value/KPIs are prompted in separate turns. `ready_for_review` is true only when the validator
  passes, every obligation is met and no question is outstanding.
* Messages that try to change the assistant's rules are treated as content: briefly redirected
  ("I can only help with completing the canvas."), nothing from them is saved, and canvas facts
  given alongside are kept. Nothing is executed against any system a user names.
* Questions are checked (`validate_question`) for exactly one `?`, no list markers, no joined
  requests, no second question word, the selected target's topic, and no other topic's strong
  keywords. A model-written question that fails is never shown; the reviewed template is used.
* **Questions are direct and about one field, in a colleague's voice** ("What makes the way it
  works today painful or frustrating?"), with a short tip after some first asks ("A rough working
  title is fine."). When an answer cannot be used, the follow-up shows how to answer with an
  example, then asks again ("Tell me the biggest headache, like “it takes two hours and details
  get lost”. What problem hurts most?").
* **Acknowledgements vary** ("Got it.", "That makes sense.", "Good to know.") and are picked from
  the turn number, so the same state always gives the same reply. The rule redirect and the
  contradiction question keep their exact wording.
* **Opportunity type lists every option** (Find Information, Generate Reports, Predict Issues,
  Recommend Actions, Analyse Data, Automate Workflows) and says more than one is fine. A plain
  answer such as "write the handover report" is mapped to the matching option when it clearly fits.
* **Priority gets one follow-up**: after the user chooses it, "Why does this deserve High
  priority? A sentence or two is plenty." The reason is the business case, so it is kept in Business Impact (joined to
  anything already there). Skipping it never blocks anything, and an answered Business Impact is
  not asked again later.

## Prompts and questions (version `2026-11-04.3`)

`prompts.py` holds the extractor prompt, an ask and a follow-up for every field, the priority
follow-up, the dedicated confirmations and the acknowledgements. Representative examples (plain language, no canvas
labels needed):

| Target | First ask | Follow-up if the answer could not be used |
| --- | --- | --- |
| Solution Name | What would you like to call this idea? (A rough working title is fine.) | A short name like “Shift Notes Assistant” works. What would you call it? |
| Current Process | Could you walk me through how this work gets done today? | Think of the steps from start to finish, like “copy the notes into a spreadsheet, then email a summary”. How is it done today? |
| Pain Points | What makes the way it works today painful or frustrating? | Tell me the biggest headache, like “it takes two hours and details get lost”. What problem hurts most? |
| Opportunity type | What kind of help do you want from AI: Find Information, …, or Automate Workflows? (You can pick more than one.) | Pick whichever fits best, like “Find Information and Generate Reports”. Which of these fit: …? |
| Data (confirmation) | I have “…” down as the data so far. Is there any other data it would need? | |
| Priority | What priority would you give this idea: Low, Medium, High or Strategic? | It's your call. Which priority fits: Low, Medium, High or Strategic? |
| Priority (follow-up) | Why does this deserve High priority? (A sentence or two is plenty.) | |
| Business Impact (optional) | What difference would this make for the business, if you can say? | It's fine to say you don't know. Roughly, what would improve for the business? |

Changing any wording means bumping `PROMPT_VERSION`; the tests check that every template passes
the one-question checker. The templates hold only instructions and synthetic examples.

## Running it (model-backed)

The interviewer uses the model through the verified Bedrock adapter. There is no model-free mode in
the application.

| `CONVERSATION_PRODUCER` | Behaviour |
| --- | --- |
| `unavailable` (default) | No assistant: replies fail with "unavailable"; nothing is fabricated. |
| `interviewer_bedrock` | The guided interview. A forced `update_canvas` tool call extracts facts; model and generation settings come from the typed Bedrock configuration. **Startup fails** (naming the setting) unless an approved selection is configured; see [bedrock.md](bedrock.md). |
| `test` | A fixed scripted sentence for presentation tests only; refused when deployed. |

To run it you need, in the same shell as the backend: AWS credentials for the workload identity or
your own profile, an approved selection in `backend/app/ai/policy.py` (with its evidence), and the
configuration for it:

```bash
export AWS_PROFILE=<profile>                 # or the task role in AWS
export BEDROCK_REGION=ap-southeast-2
export BEDROCK_MODEL_ID=<approved model or inference-profile id>
export BEDROCK_MAX_TOKENS=1024
CONVERSATION_PRODUCER=interviewer_bedrock APP_ENV=local uv run uvicorn --factory app.main:create_app --port 8000
```

A malformed, partial, unsupported or throttled tool result fails the turn (retryable, nothing
committed); there is no fallback model or region.

## Tests

The engine is deterministic code, so its tests drive it through whole conversations with a
**test double** (`AnswerFiler` in `backend/tests/interview/conftest.py`) that stands in for the
model by filing each reply under the field being asked about. It is not part of the application.
The Bedrock extractor is tested against stubbed ConverseStream events (valid, partial, malformed,
unsupported and throttled tool output). The AWS-free browser tests exercise the conversation
screen with the scripted `test` producer; the guided interview itself needs the model.

## Not done in this version

* **Unsupported-claim detection for free text** is limited to source-turn citation and the
  field/value rules; a model paraphrase is not compared against the user's words.
* **Real-model checks**: the model path has not run live: no Bedrock smoke test, no review round of generated questions,
  and no prompt regression matrix beyond the templates and the stubbed-stream tests. Do these
  before enabling `interviewer_bedrock` anywhere real.
* **Durable storage**: interview state is in process memory like the conversation store; deployed
  runs fail closed until both have a durable store.
* **Not built**: scoring, backlog submission, PDF, read-back confirmation, panel-sized writing.
