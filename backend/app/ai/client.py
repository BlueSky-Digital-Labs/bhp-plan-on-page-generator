import os

import boto3
from botocore.config import Config

from app.ai.config import BedrockConfig


def build_bedrock_runtime_client(config: BedrockConfig):
    """bedrock-runtime client using the workload role's credentials.

    When BEDROCK_PROFILE names a profile in AWS_CONFIG_FILE (a role in another account that the
    workload role assumes), the client uses that profile; every other client keeps the default
    chain. Retries apply to establishing the request (e.g. throttling). Once a stream starts the
    adapter never replays it. Credentials are never read from, or stored in, configuration.
    """
    profile = os.environ.get("BEDROCK_PROFILE")
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return session.client(
        "bedrock-runtime",
        region_name=config.region,
        config=Config(
            connect_timeout=config.connect_timeout_seconds,
            read_timeout=config.read_timeout_seconds,
            retries={"total_max_attempts": config.max_attempts, "mode": "adaptive"},
        ),
    )
