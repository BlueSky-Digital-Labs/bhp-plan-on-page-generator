import boto3
from botocore.config import Config

from app.access.secrets import EnvSecretProvider, SecretProvider, SecretsManagerProvider
from app.access.service import AccessService
from app.access.store import DynamoSessionStore, InMemorySessionStore, SessionStore
from app.config import Settings

# Fail fast: a slow dependency must not hold a request open.
_CLIENT_CONFIG = Config(
    connect_timeout=3, read_timeout=5, retries={"mode": "standard", "total_max_attempts": 3}
)


def build_access_service(
    settings: Settings,
    secret_provider: SecretProvider | None = None,
    store: SessionStore | None = None,
) -> AccessService:
    """Production wiring: Secrets Manager + DynamoDB through the workload role.

    Without the identifiers (local/test only; deployed requires them) it falls back to
    environment secrets and an in-memory store.
    """
    if secret_provider is None:
        hash_id = settings.access_code_hash_secret_id
        key_id = settings.cookie_signing_key_secret_id
        if hash_id and key_id:
            client = boto3.client(
                "secretsmanager", region_name=settings.aws_region, config=_CLIENT_CONFIG
            )
            secret_provider = SecretsManagerProvider(client, hash_id, key_id)
        else:
            secret_provider = EnvSecretProvider()
    if store is None:
        if settings.sessions_table_name:
            client = boto3.client(
                "dynamodb", region_name=settings.aws_region, config=_CLIENT_CONFIG
            )
            store = DynamoSessionStore(client, settings.sessions_table_name)
        else:
            store = InMemorySessionStore()
    return AccessService(secret_provider, store)
