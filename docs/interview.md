# Guided interview

Turns what a user says into canvas fields and asks for what is missing, **one question per
turn**. Code: `backend/app/interview/`. It plugs into the conversation stream
([conversation.md](conversation.md)) and adds no new endpoint: `GET /api/conversation` now also
returns the draft as `canvas: {fields, missing_required, ready_for_review}`, and the
conversation screen shows it beside the transcript.

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

## Prompts and questions (version `2026-11-03.1`)

`prompts.py` holds the system prompt, an ask and a follow-up for every field, the dedicated
confirmations and the acknowledgements. Representative examples (plain language, no canvas
labels needed):

| Target | First ask | Follow-up if unclear |
| --- | --- | --- |
| Solution Name | What would you like to call this solution? | A short working title is enough. What would you call this solution? |
| Current Process | How is this work done today, step by step in your own words? | Briefly, what do people actually do today to get this done? |
| Pain Points | What are the main problems or frustrations with the way it works today? | What is the single biggest problem with the current way of working? |
| Opportunity type | Which of these fits best: Find Information, … or Automate Workflows? | Please pick one or more of these: … Which ones fit? |
| Data (confirmation) | Is there other data it would need, besides “…”? | |
| Priority | Which priority would you give this idea: Low, Medium, High or Strategic? | The priority is your choice. Which one fits: Low, Medium, High or Strategic? |
| Business Impact (optional) | What difference would this make to the business, if you can say? | You can say you don't know. Roughly, what would improve for the business? |

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

* **Model-repair loop**: an invalid model-written question goes straight to the reviewed
  template (zero repair attempts).
* **Model-written questions** are accepted only if they validate against the server-chosen
  target; since the model does not know that target when extracting, templates are used almost
  always.
* **Unsupported-claim detection for free text** is limited to source-turn citation and the
  field/value rules; a model paraphrase is not compared against the user's words.
* **Real-model checks**: the model path has not run live: no Bedrock smoke test, no review round of generated questions,
  and no prompt regression matrix beyond the templates and the stubbed-stream tests. Do these
  before enabling `interviewer_bedrock` anywhere real.
* **Durable storage**: interview state is in process memory like the conversation store; deployed
  runs fail closed until both have a durable store.
* **Not built**: scoring, backlog submission, PDF, read-back confirmation, panel-sized writing.
