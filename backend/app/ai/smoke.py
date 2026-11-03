"""Synthetic ConverseStream + structured-tool smoke test.

Run it from the private application task (so it uses the workload role, the VPC
endpoints and the configured model), e.g. via ECS Exec / a one-off task:

    python -m app.ai.smoke              # streaming + tool call
    python -m app.ai.smoke --verify     # also run control-plane checks (needs a verification role)

Output is a JSON summary of pass/fail flags and codes. It never prints prompts,
completions or tool input. Exit status is 0 only if everything passed.
"""

import argparse
import json
import sys
from typing import Any, Literal

import boto3
from pydantic import BaseModel, ConfigDict

from app.ai import (
    BedrockSelectionError,
    ConverseRequest,
    ConverseStreamAdapter,
    ToolDefinition,
    ValidatedBedrockConfig,
    build_bedrock_runtime_client,
    get_validated_bedrock_config,
)
from app.ai.policy import Capability
from app.ai.verification import run_all


class SmokeToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]


SMOKE_TOOL = ToolDefinition(
    name="report_status",
    description="Report the status of a synthetic connectivity check.",
    input_model=SmokeToolInput,
)


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"text": text}]}


def run_smoke(validated: ValidatedBedrockConfig, client: Any) -> dict[str, Any]:
    adapter = ConverseStreamAdapter(client, validated)

    prompt = "Synthetic connectivity check. Reply with one short word."
    streamed = adapter.collect(ConverseRequest(messages=[_user(prompt)]))
    forced = validated.supports(Capability.FORCED_TOOL_CHOICE)
    tooled = adapter.collect(
        ConverseRequest(
            system=["You are a synthetic connectivity check. Always answer by calling the tool."],
            messages=[_user("Call report_status with status ok.")],
            tools=[SMOKE_TOOL],
            tool_choice=SMOKE_TOOL.name if forced else None,
        )
    )

    def end_summary(result: Any) -> dict[str, Any]:
        end = result.end
        return {
            "status": end.status.value,
            "stop_reason": end.stop_reason,
            "failure_code": end.failure.code.value if end.failure else None,
            "request_id": end.failure.request_id if end.failure else None,
        }

    streaming_ok = streamed.committable and bool(streamed.text.strip())
    tool_ok = (
        tooled.committable
        and len(tooled.tool_calls) == 1
        and tooled.tool_calls[0].name == SMOKE_TOOL.name
    )
    return {
        "model_id": validated.config.model_id,
        "region": validated.config.region,
        "streaming": {"ok": streaming_ok, **end_summary(streamed)},
        "tool": {"ok": tool_ok, **end_summary(tooled)},
        "ok": streaming_ok and tool_ok,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true", help="also run control-plane checks")
    args = parser.parse_args(argv)

    try:
        validated = get_validated_bedrock_config()
    except BedrockSelectionError as exc:
        print(json.dumps({"ok": False, "blocked": list(exc.codes), "settings": list(exc.settings)}))
        return 2

    summary = run_smoke(validated, build_bedrock_runtime_client(validated.config))
    if args.verify:
        bedrock = boto3.client("bedrock", region_name=validated.config.region)
        checks = run_all(bedrock, validated.selection)
        summary["verification"] = [{"name": c.name, "ok": c.ok, "code": c.code} for c in checks]
        summary["ok"] = summary["ok"] and all(c.ok for c in checks)
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
