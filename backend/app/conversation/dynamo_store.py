"""The conversation in DynamoDB: the same contract as the in-memory store, for deployed runs.

One `CONV` item per session (revision, the open turn, submissions) and one `TURN#nnnn` item per turn
(so a long conversation never meets the 400 KB item limit). Every change that must hold together is
one transaction guarded by conditions, so two tasks answering at once cannot both win, a retried
answer is recorded once, and deleting can never remove an answer that has not been seen. Reads that
feed a decision are strongly consistent. See app/dynamo.py for the table and docs/downloads.md.
"""

import json
from dataclasses import replace
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from app.access.errors import DependencyUnavailable
from app.conversation.store import MAX_TURNS, SubmitResult, Turn, _new_id, answered_context
from app.dynamo import (
    EXPIRY_INDEX,
    INDEX_PK,
    INDEX_VALUE,
    PK,
    SK,
    TTL_ATTRIBUTE,
    N,
    S,
    condition_failed,
    delete_items,
    epoch,
    guarded,
    query_all,
    transaction_cancelled,
)

_CONV = "CONV"
_TURN = "TURN#"
_ATTEMPTS = 3  # a lost race is retried from a fresh read; more than this is a fault


def _turn_key(seq: int) -> str:
    return f"{_TURN}{seq:04d}"


def _turn_from(item: dict[str, Any]) -> Turn:
    return Turn(item["turn_id"]["S"], item["role"]["S"], item["text"]["S"], item["status"]["S"])  # type: ignore[arg-type]


class DynamoConversationStore:
    def __init__(self, client: Any, table: str) -> None:
        self._client, self._table = client, table

    # --- reads -------------------------------------------------------------------------------

    def _conv(self, session_id: str) -> dict[str, Any] | None:
        with guarded():
            item = self._client.get_item(
                TableName=self._table,
                Key={PK: S(session_id), SK: S(_CONV)},
                ConsistentRead=True,
            ).get("Item")
        return item or None

    def _turns(self, session_id: str) -> list[tuple[int, Turn]]:
        """(sequence number, turn) for every turn, oldest first."""
        with guarded():
            items = query_all(
                self._client,
                TableName=self._table,
                KeyConditionExpression="#p = :p AND begins_with(#s, :t)",
                ExpressionAttributeNames={"#p": PK, "#s": SK},
                ExpressionAttributeValues={":p": S(session_id), ":t": S(_TURN)},
                ConsistentRead=True,
            )
        try:
            found = [(int(i[SK]["S"][len(_TURN) :]), _turn_from(i)) for i in items]
        except (KeyError, ValueError):
            raise DependencyUnavailable("store_record_invalid") from None
        return sorted(found, key=lambda pair: pair[0])

    def history(self, session_id: str) -> tuple[int, list[Turn]]:
        conv = self._conv(session_id)
        if conv is None:
            return 0, []
        return int(conv["revision"]["N"]), [turn for _, turn in self._turns(session_id)]

    # --- the answer, atomically and at most once -----------------------------------------------

    def submit(
        self,
        session_id: str,
        submission_id: str,
        text: str,
        expected_revision: int,
        session_expires_at: datetime,
        now: datetime,
    ) -> SubmitResult:
        for _ in range(_ATTEMPTS):
            result = self._submit_once(
                session_id, submission_id, text, expected_revision, session_expires_at
            )
            if result is not None:
                return result
        raise DependencyUnavailable("store_contention")

    def _submit_once(
        self,
        session_id: str,
        submission_id: str,
        text: str,
        expected_revision: int,
        session_expires_at: datetime,
    ) -> SubmitResult | None:
        conv = self._conv(session_id)
        revision = int(conv["revision"]["N"]) if conv else 0
        count = int(conv["turn_count"]["N"]) if conv else 0

        replayed = conv["submissions"]["M"].get(submission_id) if conv else None
        if replayed is not None:  # a retry of an already-recorded answer
            first = int(replayed["M"]["s"]["N"])
            by_seq = {seq: turn for seq, turn in self._turns(session_id)}
            return SubmitResult("replayed", revision, by_seq[first], by_seq[first + 1])
        if expected_revision != revision:
            return SubmitResult("revision_conflict", revision)
        if conv and ("open_turn" in conv or "purging" in conv):
            return SubmitResult(
                "revision_conflict" if "purging" in conv else "unfinished", revision
            )
        if count + 2 > MAX_TURNS:
            return SubmitResult("full", revision)

        user = Turn(_new_id(), "user", text, "complete")
        assistant = Turn(_new_id(), "assistant", "", "pending")
        first = count + 1
        expires = N(epoch(session_expires_at))
        pair = {"M": {"u": S(user.turn_id), "a": S(assistant.turn_id), "s": N(first)}}

        def turn_put(seq: int, turn: Turn) -> dict[str, Any]:
            return {
                "Put": {
                    "TableName": self._table,
                    "Item": {
                        PK: S(session_id),
                        SK: S(_turn_key(seq)),
                        "turn_id": S(turn.turn_id),
                        "role": S(turn.role),
                        "text": S(turn.text),
                        "status": S(turn.status),
                        TTL_ATTRIBUTE: expires,
                    },
                    "ConditionExpression": "attribute_not_exists(#p)",
                    "ExpressionAttributeNames": {"#p": PK},
                }
            }

        if conv is None:
            conv_write: dict[str, Any] = {
                "Put": {
                    "TableName": self._table,
                    "Item": {
                        PK: S(session_id),
                        SK: S(_CONV),
                        "revision": N(1),
                        "turn_count": N(2),
                        "open_turn": S(assistant.turn_id),
                        "submissions": {"M": {submission_id: pair}},
                        INDEX_PK: S(INDEX_VALUE),
                        TTL_ATTRIBUTE: expires,
                    },
                    "ConditionExpression": "attribute_not_exists(#p)",
                    "ExpressionAttributeNames": {"#p": PK},
                }
            }
        else:
            conv_write = {
                "Update": {
                    "TableName": self._table,
                    "Key": {PK: S(session_id), SK: S(_CONV)},
                    "UpdateExpression": (
                        "SET #r = :new, turn_count = :tc, open_turn = :open, #subs.#sid = :pair"
                    ),
                    "ConditionExpression": (
                        "#r = :old AND attribute_not_exists(open_turn) "
                        "AND attribute_not_exists(purging)"
                    ),
                    "ExpressionAttributeNames": {
                        "#r": "revision",
                        "#subs": "submissions",
                        "#sid": submission_id,
                    },
                    "ExpressionAttributeValues": {
                        ":new": N(revision + 1),
                        ":old": N(revision),
                        ":tc": N(count + 2),
                        ":open": S(assistant.turn_id),
                        ":pair": pair,
                    },
                }
            }
        try:
            with guarded(expect=("TransactionCanceledException",)):
                self._client.transact_write_items(
                    TransactItems=[
                        conv_write,
                        turn_put(first, user),
                        turn_put(first + 1, assistant),
                    ]
                )
        except ClientError as exc:
            if transaction_cancelled(exc):
                return None  # someone else got there first: look again
            raise
        return SubmitResult("created", revision + 1, user, assistant)

    # --- turns -----------------------------------------------------------------------------------

    def _find(self, session_id: str, turn_id: str) -> tuple[int, Turn] | None:
        return next(
            ((seq, turn) for seq, turn in self._turns(session_id) if turn.turn_id == turn_id), None
        )

    def get_turn(self, session_id: str, turn_id: str) -> Turn | None:
        found = self._find(session_id, turn_id)
        return found[1] if found else None

    def context_for(self, session_id: str, turn_id: str) -> tuple[str, str, tuple[str, ...]]:
        """(answered user turn id, its text, all user turn ids) for an assistant turn."""
        return answered_context([turn for _, turn in self._turns(session_id)], turn_id)

    def complete_turn(self, session_id: str, turn_id: str, text: str) -> tuple[Turn | None, bool]:
        """Commit the assistant's reply. Set-once; the flag says whether this call did it."""
        found = self._find(session_id, turn_id)
        if found is None or found[1].status == "complete":
            return (found[1] if found else None), False
        seq, turn = found
        try:
            with guarded(expect=("TransactionCanceledException",)):
                self._client.transact_write_items(
                    TransactItems=[
                        {
                            "Update": {
                                "TableName": self._table,
                                "Key": {PK: S(session_id), SK: S(_turn_key(seq))},
                                "UpdateExpression": "SET #t = :text, #st = :done",
                                "ConditionExpression": "#st <> :done",
                                "ExpressionAttributeNames": {"#t": "text", "#st": "status"},
                                "ExpressionAttributeValues": {
                                    ":text": S(text),
                                    ":done": S("complete"),
                                },
                            }
                        },
                        {
                            "Update": {
                                "TableName": self._table,
                                "Key": {PK: S(session_id), SK: S(_CONV)},
                                "UpdateExpression": "REMOVE open_turn",
                                "ConditionExpression": "open_turn = :id",
                                "ExpressionAttributeValues": {":id": S(turn_id)},
                            }
                        },
                    ]
                )
        except ClientError as exc:
            if transaction_cancelled(exc):
                again = self._find(session_id, turn_id)  # lost the race, or the session is gone
                return (again[1] if again else None), False
            raise
        return replace(turn, text=text, status="complete"), True

    def mark(self, session_id: str, turn_id: str, status: str) -> None:
        """Move a not-yet-complete assistant turn between pending and failed."""
        found = self._find(session_id, turn_id)
        if found is None or found[1].status == "complete":
            return
        try:
            with guarded(expect=("ConditionalCheckFailedException",)):
                self._client.update_item(
                    TableName=self._table,
                    Key={PK: S(session_id), SK: S(_turn_key(found[0]))},
                    UpdateExpression="SET #st = :s, #t = :empty",
                    ConditionExpression="#st <> :done",
                    ExpressionAttributeNames={"#st": "status", "#t": "text"},
                    ExpressionAttributeValues={
                        ":s": S(status),
                        ":empty": S(""),
                        ":done": S("complete"),
                    },
                )
            return
        except ClientError as exc:
            if condition_failed(exc):
                return  # it completed meanwhile: leave it
            raise

    def touch(self, session_id: str, expires_at: datetime) -> None:
        """Move the content's expiry (the idle deadline), which is what the sweep looks at. Only for
        content that exists; the other items keep their 24 h expiry and go with the session."""
        try:
            with guarded(expect=("ConditionalCheckFailedException",)):
                self._client.update_item(
                    TableName=self._table,
                    Key={PK: S(session_id), SK: S(_CONV)},
                    UpdateExpression="SET #e = :e",
                    ConditionExpression="attribute_exists(#p) AND attribute_not_exists(purging)",
                    ExpressionAttributeNames={"#e": TTL_ATTRIBUTE, "#p": PK},
                    ExpressionAttributeValues={":e": N(epoch(expires_at))},
                )
        except ClientError as exc:
            if not condition_failed(exc):
                raise

    # --- deleting --------------------------------------------------------------------------------

    def delete_session(self, session_id: str) -> None:
        """Everything this store holds for the session. Safe to repeat if it stopped half way."""
        keys = [{PK: S(session_id), SK: S(_CONV)}]
        keys += [{PK: S(session_id), SK: S(_turn_key(seq))} for seq, _ in self._turns(session_id)]
        with guarded():
            delete_items(self._client, self._table, keys)

    def delete_session_if_revision(self, session_id: str, revision: int) -> bool:
        """Delete only if no answer has arrived since `revision`.

        One conditional step first marks the session as being deleted (no answer or reply can be
        written after that), then the items are removed; if that is interrupted, asking again
        finishes it. False, and nothing changed, if an answer has arrived.
        """
        try:
            with guarded(expect=("ConditionalCheckFailedException",)):
                self._client.update_item(
                    TableName=self._table,
                    Key={PK: S(session_id), SK: S(_CONV)},
                    UpdateExpression="SET purging = :yes",
                    ConditionExpression="attribute_exists(#p) AND #r = :r",
                    ExpressionAttributeNames={"#p": PK, "#r": "revision"},
                    ExpressionAttributeValues={":yes": S("yes"), ":r": N(revision)},
                )
        except ClientError as exc:
            if not condition_failed(exc):
                raise
            if self._conv(session_id) is not None or revision != 0:
                return False  # the revision has moved on
        self.delete_session(session_id)
        return True

    def expired_session_ids(
        self, now: datetime, limit: int, after: str | None = None
    ) -> tuple[list[str], str | None]:
        """One page of sessions past their end (strictly before `now`): 24 h after they began, or
        the idle limit after their last activity, whichever came first.

        Read from the sparse expiry index: ids and times only, never content. `after` is the opaque
        token from the previous page.
        """
        request: dict[str, Any] = {
            "TableName": self._table,
            "IndexName": EXPIRY_INDEX,
            "KeyConditionExpression": "#g = :g AND #e < :now",
            "ExpressionAttributeNames": {"#g": INDEX_PK, "#e": TTL_ATTRIBUTE},
            "ExpressionAttributeValues": {":g": S(INDEX_VALUE), ":now": N(epoch(now))},
            "Limit": limit,
        }
        if after is not None:
            try:
                start = json.loads(after)
                if not isinstance(start, dict) or set(start) != {PK, SK, INDEX_PK, TTL_ATTRIBUTE}:
                    raise ValueError
            except ValueError:
                raise DependencyUnavailable("bad_page_token") from None
            request["ExclusiveStartKey"] = start
        with guarded():
            page = self._client.query(**request)
        ids = [item[PK]["S"] for item in page.get("Items", [])]
        token = json.dumps(page["LastEvaluatedKey"]) if "LastEvaluatedKey" in page else None
        return ids, token
