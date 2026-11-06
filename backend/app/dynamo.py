"""The one DynamoDB table every store shares, and the few helpers they all need.

Single table, partition key `session_id`, sort key `sk`, TTL on `expires_at`. Item types:

    session_id            sk                 what it is                          written by
    <session id>          ACCESS             the sign-in record                  access store
    grant#<grant id>      GRANT              a used one-time grant               access store
    <session id>          CONV               revision, open turn, submissions    conversation store
    <session id>          TURN#0001 ...      one turn each (user, assistant)     conversation store
    <session id>          INTERVIEW          the canvas draft (JSON)             interview store
    <session id>          DELIVERY#canvas    last revision of a PDF fully sent   delivery store
    <session id>          DELIVERY#appendix  (same)                              delivery store

Only CONV items carry `gsi_pk = "SESSIONS"`, which puts them in the sparse `expiry-index`
(partition `gsi_pk`, sort `expires_at`): ids and times only, never content. It is how the scheduled
sweep finds expired sessions. `infra/` creates the same table (docs/deployment.md); the tests build
it from `table_definition` below, so the code and the table cannot drift apart unnoticed.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.access.errors import DependencyUnavailable

PK = "session_id"
SK = "sk"
TTL_ATTRIBUTE = "expires_at"
EXPIRY_INDEX = "expiry-index"
INDEX_PK = "gsi_pk"
INDEX_VALUE = "SESSIONS"

# Fail fast: a slow dependency must not hold a request open. Retries are for throttling only.
CLIENT_CONFIG = Config(
    connect_timeout=3, read_timeout=5, retries={"mode": "standard", "total_max_attempts": 3}
)


def build_client(region: str):
    """DynamoDB client on the default credential chain (the workload role)."""
    return boto3.client("dynamodb", region_name=region, config=CLIENT_CONFIG)


def table_definition(name: str) -> dict[str, Any]:
    """`create_table` arguments for the table the app expects (used by the tests)."""
    return {
        "TableName": name,
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [
            {"AttributeName": PK, "AttributeType": "S"},
            {"AttributeName": SK, "AttributeType": "S"},
            {"AttributeName": INDEX_PK, "AttributeType": "S"},
            {"AttributeName": TTL_ATTRIBUTE, "AttributeType": "N"},
        ],
        "KeySchema": [
            {"AttributeName": PK, "KeyType": "HASH"},
            {"AttributeName": SK, "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": EXPIRY_INDEX,
                "KeySchema": [
                    {"AttributeName": INDEX_PK, "KeyType": "HASH"},
                    {"AttributeName": TTL_ATTRIBUTE, "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            }
        ],
    }


def S(value: str) -> dict[str, str]:  # noqa: N802 - mirrors DynamoDB's own type tags
    return {"S": value}


def N(value: int | float) -> dict[str, str]:  # noqa: N802
    return {"N": str(int(value)) if float(value).is_integer() else str(value)}


def epoch(moment) -> int:
    return int(moment.timestamp())


def error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def condition_failed(exc: ClientError) -> bool:
    return error_code(exc) == "ConditionalCheckFailedException"


def transaction_cancelled(exc: ClientError) -> bool:
    return error_code(exc) == "TransactionCanceledException"


@contextmanager
def guarded(*, expect: tuple[str, ...] = ()) -> Iterator[None]:
    """Any storage failure becomes one fixed, content-free error (the caller fails closed).

    Errors named in `expect` (a refused condition, for example) are re-raised untouched for the
    caller to handle.
    """
    try:
        yield
    except ClientError as exc:
        if error_code(exc) in expect:
            raise
        raise DependencyUnavailable("store") from None
    except BotoCoreError:
        raise DependencyUnavailable("store") from None


def query_all(client: Any, **request: Any) -> list[dict[str, Any]]:
    """Every item of a query, following pages."""
    items: list[dict[str, Any]] = []
    while True:
        page = client.query(**request)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return items
        request["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def delete_items(client: Any, table: str, keys: list[dict[str, Any]]) -> None:
    """Delete items by key, 25 per request, repeating whatever DynamoDB left unprocessed."""
    for start in range(0, len(keys), 25):
        pending = [{"DeleteRequest": {"Key": key}} for key in keys[start : start + 25]]
        for _ in range(8):
            if not pending:
                break
            pending = (
                client.batch_write_item(RequestItems={table: pending})
                .get("UnprocessedItems", {})
                .get(table, [])
            )
        if pending:
            raise DependencyUnavailable("store")  # still not deleted: the caller will retry
