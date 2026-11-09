# Guided interview

Turns what a user says into canvas fields and asks for what is missing, **one question per
turn**. Code: `backend/app/interview/`. It plugs into the conversation stream
([conversation.md](conversation.md)) and adds no new endpoint: `GET /api/conversation` now also
returns the draft as `canvas: {fields, missing_required, ready_for_review}` and the interview's
position as `progression` (below). The conversation screen starts as one centred chat box. Once the first answer is in, the page
widens: the **Save your progress** card ([downloads.md](downloads.md)) sits beside the chat (above it on
a phone) while the canvas is unfinished, and **Your canvas so far** (a progress bar and a small,
keyboard-scrollable box of the fields) appears below the chat. While a reply is coming, the card's
place stays and says saving is available once the assistant has replied.
When the server says the canvas is ready and the assistant has sent its closing message, a
**Generate canvas** button appears in the chat under that message; the answer box stays.

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

### Questions with a fixed list of answers

Two fields are answered from a fixed list: **Priority** (exactly one of Must-have, Nice-to-have,
Exploratory) and **Opportunity types** (one or more of Find Information, Generate Reports, Predict
Issues, Recommend Actions, Analyse Data, Automate Workflows). For these, `outstanding_question`
also carries `choices: {"multiple": bool, "options": [...]}` (`choices_for` in `engine.py`, from the
same lists the validator accepts); for every other question `choices` is `null`.

The conversation screen shows the options as radio buttons (or checkboxes) with a **Send** button
instead of the text box (`ChoiceAnswer.tsx`), and a **Type my own answer instead** button to go back
to the text box. Picking sends the option's own wording ("Must-have", or "Find Information, Predict
Issues" in the list's order) through the normal answer route, so it is exactly the user's typed
answer and every rule below still applies, including that the priority is only ever the user's own
explicit choice. The list is offered only when the question asks for the field itself ("missing",
or asked again because the answer was unclear) and the assistant has finished replying; the
priority follow-up ("why that priority?") and a choice between an earlier and a new value are
typed. The browser has no copy of the lists: it shows whatever the server sends.

### Changing something after "That's everything I need"

The user can keep talking until they confirm the canvas on the review page. A different value for an
answered field is asked about first (the contradiction rule below), then applied when they choose it.
The priority can be changed too, but only when the user's own words say "priority" and name the new
one. A message that cannot be read as a change gets a fixed reply saying how to ask for one (“Change
the team to Operations”), not the closing line again. Opportunity types can be added to, not removed.

Once the user confirms the canvas ("Yes, this is correct"), it is **final**: the server refuses
further answers (`409 canvas_confirmed`), the review page has no way back to the conversation, and
`/conversation` goes straight back to the review page. Before confirming, nothing is final.

## Rules the engine enforces (each has a test)

* Only the 16 canvas ids (same as `frontend/src/canvas/fields.ts`); only the six opportunity
  types; priority only Must-have/Nice-to-have/Exploratory (typed as "must have" or "nice to have" is understood too). Unsupported fields or values are **rejected,
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
* **Questions are specific, lead with concrete things to think about, and ask about one field**
  ("Which part of the way it works today causes the most problems, such as delays, mistakes or
  rework?"). After a first ask a short **hint** shows how to answer well, with a synthetic example
  ("Numbers help, like “two hours every shift” or “three errors a week”."). When an answer cannot
  be used, the follow-up shows how to answer with an example, then asks again.
* **Acknowledgements vary** ("Got it.", "That makes sense.", "Good to know.") and are picked from
  the turn number, so the same state always gives the same reply. The rule redirect and the
  contradiction question keep their exact wording.
* **Opportunity type lists every option** (Find Information, Generate Reports, Predict Issues,
  Recommend Actions, Analyse Data, Automate Workflows) and says more than one is fine. A plain
  answer such as "write the handover report" is mapped to the matching option when it clearly fits.
* **Priority has three defined options**: Must-have, Nice-to-have or Exploratory, with a hint that
  explains each (Must-have: the work really needs it; Nice-to-have: it helps but can wait;
  Exploratory: worth testing first). The old Low/Medium/High/Strategic values are rejected.
* **Priority gets one follow-up**: after the user chooses it, "Why did you give this idea the
  priority “Must-have”? A sentence or two is plenty." The reason is the business case, so it is kept in Business Impact (joined to
  anything already there). Skipping it never blocks anything, and an answered Business Impact is
  not asked again later.

## Prompts and questions (version `2026-11-09.1`)

`prompts.py` holds the extractor prompt, an ask, a follow-up and a how-to-answer hint for every field,
the priority follow-up, the dedicated confirmations and the acknowledgements. Representative examples:

| Target | First ask (then a hint) | Follow-up if the answer could not be used |
| --- | --- | --- |
| Current Process | Could you walk me through how this work gets done today, from the first step to the last? (Say who does each step and what they use.) | Think of the steps from start to finish, like “copy the notes into a spreadsheet, then email a summary”. How is it done today? |
| Pain Points | Which part of the way it works today causes the most problems, such as delays, mistakes or rework? (Numbers help.) | Tell me the biggest headache, like “it takes two hours and details get lost”. What problem hurts most? |
| Success | What is one change you would notice that tells you this is working? (Something you could see or count.) | Think of one change you'd notice, like “handover takes minutes and nothing is missed”. What would success look like? |
| Data | What data or documents would it need to read, such as logs, reports or records? | Name the records or documents involved, like “shift logs and incident reports”. What data would it need? |
| Risks | What risks or dependencies could stop this from working, such as approvals, sensitive information or other groups? | Name the one thing most likely to cause trouble, like “notes may contain sensitive details”. What risk worries you most? |
| Opportunity type | What kind of help do you want from AI: Find Information, …, or Automate Workflows? (You can pick more than one.) | Pick whichever fits best, like “Find Information and Generate Reports”. Which of these fit: …? |
| Priority | What priority would you give this idea: Must-have, Nice-to-have or Exploratory? (Must-have means the work really needs it, Nice-to-have means it helps but can wait, Exploratory means it is worth testing first.) | It's your call. Which priority fits: Must-have, Nice-to-have or Exploratory? |
| Priority (follow-up) | Why did you give this idea the priority “Must-have”? (A sentence or two is plenty.) | |
| Business Impact (optional) | What difference would this make for the business, in time saved, cost, safety or quality? | It's fine to say you don't know. Roughly, what would improve for the business? |
| Data (confirmation) | I have “…” down as the data so far. Is there any other data it would need? | |

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
* **Durable storage**: interview state is in process memory locally; deployed it is the `INTERVIEW`
  item in the DynamoDB table (`DynamoInterviewStore`, [downloads.md](downloads.md)).
* **Not built**: scoring, backlog submission, PDF, read-back confirmation, panel-sized writing.
