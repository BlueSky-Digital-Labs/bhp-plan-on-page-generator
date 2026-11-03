import boto3
from botocore.config import Config

from app.ai.config import BedrockConfig


def build_bedrock_runtime_client(config: BedrockConfig):
    """bedrock-runtime client using the default credential chain (the workload role).

    Retries apply to establishing the request (e.g. throttling). Once a stream starts the
    adapter never replays it. Credentials are never read from, or stored in, configuration.
    """
    return boto3.client(
        "bedrock-runtime",
        region_name=config.region,
        config=Config(
            connect_timeout=config.connect_timeout_seconds,
            read_timeout=config.read_timeout_seconds,
            retries={"total_max_attempts": config.max_attempts, "mode": "adaptive"},
        ),
    )
