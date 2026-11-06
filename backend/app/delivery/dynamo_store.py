"""Delivery records in DynamoDB: one `DELIVERY#<kind>` item per document, written conditionally."""

from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError

from app.access.errors import DependencyUnavailable
from app.delivery.store import DOCUMENT_KINDS, Delivery, DocumentKind
from app.dynamo import PK, SK, TTL_ATTRIBUTE, N, S, condition_failed, epoch, guarded, query_all

_PREFIX = "DELIVERY#"


class DynamoDeliveryStore:
    def __init__(self, client: Any, table: str) -> None:
        self._client, self._table = client, table

    def record(
        self,
        session_id: str,
        kind: DocumentKind,
        revision: int,
        completed_at: datetime,
        expires_at: datetime,
    ) -> bool:
        """One conditional write: only if nothing is held or `revision` is newer. Repeating the
        same revision is harmless (the first time is kept); an older one is ignored."""
        key = {PK: S(session_id), SK: S(_PREFIX + kind)}
        try:
            with guarded(expect=("ConditionalCheckFailedException",)):
                self._client.update_item(
                    TableName=self._table,
                    Key=key,
                    UpdateExpression="SET #r = :r, completed_at = :t, #e = :e",
                    ConditionExpression="attribute_not_exists(#r) OR #r < :r",
                    ExpressionAttributeNames={"#r": "revision", "#e": TTL_ATTRIBUTE},
                    ExpressionAttributeValues={
                        ":r": N(revision),
                        ":t": S(completed_at.isoformat()),
                        ":e": N(epoch(expires_at)),
                    },
                )
            return True
        except ClientError as exc:
            if not condition_failed(exc):
                raise
        held = self.get(session_id).get(kind)
        return held is not None and held.revision == revision

    def get(self, session_id: str) -> dict[DocumentKind, Delivery]:
        with guarded():
            items = query_all(
                self._client,
                TableName=self._table,
                KeyConditionExpression="#p = :p AND begins_with(#s, :d)",
                ExpressionAttributeNames={"#p": PK, "#s": SK},
                ExpressionAttributeValues={":p": S(session_id), ":d": S(_PREFIX)},
                ConsistentRead=True,
            )
        found: dict[DocumentKind, Delivery] = {}
        for item in items:
            try:
                kind = item[SK]["S"][len(_PREFIX) :]
                if kind not in DOCUMENT_KINDS:
                    continue
                found[kind] = Delivery(  # type: ignore[index]
                    kind,  # type: ignore[arg-type]
                    int(item["revision"]["N"]),
                    datetime.fromisoformat(item["completed_at"]["S"]).astimezone(UTC),
                )
            except (KeyError, ValueError):
                raise DependencyUnavailable("store_record_invalid") from None
        return found

    def delete_session(self, session_id: str) -> None:
        with guarded():
            for kind in DOCUMENT_KINDS:
                self._client.delete_item(
                    TableName=self._table, Key={PK: S(session_id), SK: S(_PREFIX + kind)}
                )
