"""The interview state in DynamoDB: one `INTERVIEW` item per session holding the draft as JSON."""

from datetime import datetime
from typing import Any

from app.access.errors import DependencyUnavailable
from app.dynamo import PK, SK, TTL_ATTRIBUTE, N, S, epoch, guarded
from app.interview.engine import InterviewState
from app.interview.state_json import dump_state, load_state
from app.interview.store import InterviewStoreBase

_SORT = "INTERVIEW"


class DynamoInterviewStore(InterviewStoreBase):
    def __init__(self, client: Any, table: str) -> None:
        self._client, self._table = client, table

    def get(self, session_id: str) -> InterviewState:
        with guarded():
            item = self._client.get_item(
                TableName=self._table, Key={PK: S(session_id), SK: S(_SORT)}, ConsistentRead=True
            ).get("Item")
        if not item:
            return InterviewState()
        try:
            return load_state(item["state"]["S"])
        except (KeyError, ValueError, TypeError):
            raise DependencyUnavailable("store_record_invalid") from None  # never a blank draft

    def put(
        self, session_id: str, state: InterviewState, expires_at: datetime, now: datetime
    ) -> None:
        with guarded():
            self._client.put_item(
                TableName=self._table,
                Item={
                    PK: S(session_id),
                    SK: S(_SORT),
                    "state": S(dump_state(state)),
                    TTL_ATTRIBUTE: N(epoch(expires_at)),
                },
            )

    def delete_session(self, session_id: str) -> None:
        with guarded():
            self._client.delete_item(TableName=self._table, Key={PK: S(session_id), SK: S(_SORT)})
