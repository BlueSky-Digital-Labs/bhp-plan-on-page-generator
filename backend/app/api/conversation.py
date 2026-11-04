"""Conversation history, answer submission and the reply stream (JSON / NDJSON, never HTML)."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.access.store import Session
from app.api.access import current_session, require_csrf
from app.conversation.service import ConversationService, turn_json
from app.errors import ApiProblem

router = APIRouter()

_TURN_ID = re.compile(r"^[a-f0-9]{32}$")
MAX_ANSWER_CHARS = 4000


def get_conversation(request: Request) -> ConversationService:
    return request.app.state.conversation


class SubmitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9-]{8,64}$")]
    expected_revision: Annotated[int, Field(ge=0)]
    text: Annotated[str, Field(min_length=1, max_length=MAX_ANSWER_CHARS)]

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


_CONFLICTS = {
    "revision_conflict": (
        "Your conversation has changed. Reload to see the latest, then send again."
    ),
    "unfinished": "The assistant has not finished replying to your last answer.",
    "full": "This conversation has reached its limit.",
}


@router.get("/conversation")
def read_conversation(
    session: Annotated[Session, Depends(current_session)],
    service: Annotated[ConversationService, Depends(get_conversation)],
) -> dict:
    return service.history(session)


@router.post("/conversation/turns", status_code=201, dependencies=[Depends(require_csrf)])
def submit_turn(
    body: SubmitBody,
    response: Response,
    session: Annotated[Session, Depends(current_session)],
    service: Annotated[ConversationService, Depends(get_conversation)],
) -> dict:
    result = service.submit(session, body.submission_id, body.text, body.expected_revision)
    if result.outcome in _CONFLICTS:
        code = {"unfinished": "turn_in_progress", "full": "conversation_full"}.get(
            result.outcome, result.outcome
        )
        raise ApiProblem(409, code, _CONFLICTS[result.outcome])
    if result.outcome == "replayed":
        response.status_code = 200  # already recorded: report it, create nothing new
    assert result.user_turn and result.assistant_turn
    return {
        "revision": result.revision,
        "user_turn": turn_json(result.user_turn),
        "assistant_turn": turn_json(result.assistant_turn),
    }


@router.get("/conversation/turns/{turn_id}/stream")
def stream_turn(
    turn_id: str,
    session: Annotated[Session, Depends(current_session)],
    service: Annotated[ConversationService, Depends(get_conversation)],
) -> StreamingResponse:
    # Unknown, malformed and other-session ids all look identical: nothing reveals ownership.
    turn = service.get_assistant_turn(session, turn_id) if _TURN_ID.match(turn_id) else None
    if turn is None:
        raise ApiProblem(404, "not_found", "The requested resource was not found.")
    return StreamingResponse(
        service.stream(session, turn),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
