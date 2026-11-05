# Amazon Bedrock integration

A provider-agnostic ConverseStream adapter plus the configuration and approval policy
that decide which model it may call. Code: `backend/app/ai/`.

> ## Status: BLOCKED until a model is verified and approved
>
> No model or inference profile has been verified yet, so **none is configured or
> approved**. The adapter fails safely (`no_selection` / `unapproved_model`) and feature
> work must not depend on it until:
>
> 1. an initial selection is approved with the evidence below, **and**
> 2. `python -m app.ai.smoke` passes from the private application task.
>
> Australian processing and no-provider-prompt-storage/training are release
> dependencies. If either cannot be verified, the integration stays blocked.

## How it fits together

| Piece | File | Job |
| --- | --- | --- |
| `BedrockConfig`, `InferenceConfig` | `app/ai/config.py` | Typed region, `model_id`, inference settings, provider-specific fields. Serialises to `modelId` / `inferenceConfig` / `additionalModelRequestFields`, omitting anything unset. |
| `DEFAULT_BEDROCK_CONFIG`, `BEDROCK_*` | `app/ai/config.py` | The central code selection and the deployment overrides. |
| `ApprovedSelection`, `APPROVED_SELECTIONS` | `app/ai/policy.py` | The only models that may be used, with dated evidence and per-model limits. |
| `ConverseStreamAdapter` | `app/ai/adapter.py` | Stateless adapter. Takes a validated configuration; never names a model. |
| `verification.py`, `smoke.py` | `app/ai/` | Control-plane checks and the synthetic streaming + tool smoke test. |

Selecting a model is two deliberate steps: *configure* it (code or env) and *approve* it
(policy entry with evidence). Configuring an unapproved model is rejected. There is no
fallback: the adapter never substitutes another model, region or provider.

## Configuration and precedence

Settings are developer/operator settings. No route, request field or user input reaches
them.

Highest precedence first:

1. **`BEDROCK_*` environment variables**: only the ones that are set override.
2. **`DEFAULT_BEDROCK_CONFIG`** in `backend/app/ai/config.py`.
3. Nothing: `BedrockSelectionError(no_selection)`.

Then the result must pass `app/ai/policy.py` (approved identifier, Australian region,
per-model ranges, compatible combinations, allowed provider fields).

| Environment variable | Maps to | Notes |
| --- | --- | --- |
| `BEDROCK_REGION` | `region` | Must be an approved Australian client region. |
| `BEDROCK_MODEL_ID` | `model_id` | Model or inference-profile id/ARN. |
| `BEDROCK_MAX_TOKENS` | `inference_config.max_tokens` | Required by policy by default (an unset value reserves the model maximum of quota). |
| `BEDROCK_TEMPERATURE` | `inference_config.temperature` | |
| `BEDROCK_TOP_P` | `inference_config.top_p` | |
| `BEDROCK_STOP_SEQUENCES` | `inference_config.stop_sequences` | JSON list. |
| `BEDROCK_ADDITIONAL_MODEL_REQUEST_FIELDS` | `additional_model_request_fields` | JSON object. Provider-specific; top-level keys must be on the model's allow-list. |
| `BEDROCK_CONNECT_TIMEOUT_SECONDS`, `BEDROCK_READ_TIMEOUT_SECONDS` | client timeouts | |
| `BEDROCK_MAX_ATTEMPTS` | total tries (1-5) | Covers establishing the request only. A started stream is never replayed. |

Unset settings are **omitted** from the request, never sent as defaults. A setting the
selected model does not support is **rejected**, never silently dropped.

Errors name settings and stable codes only (`no_selection`, `unapproved_model`,
`invalid_setting`, `region_not_approved`, `missing_setting`, `unsupported_setting`,
`incompatible_settings`), never values, prompts, credentials or provider text.

### Examples

Change the generation settings in code (no adapter or caller edits):

```python
# backend/app/ai/config.py
DEFAULT_BEDROCK_CONFIG = BedrockConfig(
    region="ap-southeast-2",
    model_id="<an approved model or inference-profile id>",
    inference_config=InferenceConfig(max_tokens=2048, temperature=0.3, stop_sequences=("</answer>",)),
    additional_model_request_fields={"<provider field>": "<value>"},  # only if allow-listed
)
```

Change one setting for a deployment without touching code:

```bash
BEDROCK_TEMPERATURE=0.1
BEDROCK_MAX_TOKENS=1500
BEDROCK_STOP_SEQUENCES='["</answer>"]'
```

Switch model: follow [Changing the model](#changing-the-model-or-profile) below. Changing
only the identifier will be rejected until the new model is approved.

Use the adapter (a caller never mentions a model):

```python
from app.ai import (ConverseRequest, ConverseStreamAdapter, ToolDefinition,
                    build_bedrock_runtime_client, get_validated_bedrock_config)

validated = get_validated_bedrock_config()          # raises BedrockSelectionError if blocked
adapter = ConverseStreamAdapter(build_bedrock_runtime_client(validated.config), validated)

class FieldUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str

tool = ToolDefinition("write_field", "Write one canvas field.", FieldUpdate)
request = ConverseRequest(
    system=["..."], messages=[{"role": "user", "content": [{"text": "..."}]}], tools=[tool],
)
for event in adapter.stream(request):   # TextDelta | ToolCall | ToolInputRejected | StreamEnd
    ...
```

`StreamEnd` is always last. **Only `status == COMPLETE` may be committed.** A stream cut
off after partial output is `INCOMPLETE`; a call that produced nothing is `FAILED`. A
tool call that fails validation is reported as `ToolInputRejected` (reason code only) and
also makes the turn `INCOMPLETE` (`invalid_tool_input`). Truncation (`max_tokens`),
malformed model output, blocked content and unknown stop reasons are all `INCOMPLETE`.
`get_validated_bedrock_config()` is called by the features that use Bedrock, not at app
startup, so the foundation app keeps running before a model is approved.

## Approving an initial selection (do this with the AWS test account)

Use authorised access, in the Sydney Region unless the evidence says otherwise. Record
everything in the register below.

1. **Candidates.** Models and inference profiles available to the account:
   ```bash
   aws bedrock list-foundation-models --region ap-southeast-2 \
     --query 'modelSummaries[].{id:modelId,stream:responseStreamingSupported,types:inferenceTypesSupported}'
   aws bedrock list-inference-profiles --region ap-southeast-2 \
     --query 'inferenceProfileSummaries[].{id:inferenceProfileId,name:inferenceProfileName,type:type}'
   ```
2. **Every possible destination.** For an inference profile, list all regions it can
   route to. The client region alone is not enough:
   ```bash
   aws bedrock get-inference-profile --region ap-southeast-2 \
     --inference-profile-identifier <profile-id> --query 'models[].modelArn'
   ```
   Each ARN's region must be Australian (`ap-southeast-2`, `ap-southeast-4`). Reject any
   profile that includes another region. A direct foundation-model id is processed in the
   client region. Cross-check against the AWS documentation for the profile
   (<https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html>)
   and record both.
3. **Capabilities.** Confirm ConverseStream (`responseStreamingSupported`) and tool use for
   the exact model in the Bedrock user guide's "Supported models and model features"
   table for the Converse API, and whether it supports forced `toolChoice`. Note its limits: max output tokens,
   temperature / top-p ranges, whether temperature and top-p may be combined, stop
   sequence limit, and any provider-specific fields you want to allow.
4. **Privacy.** Record the authoritative statement that Bedrock does not store prompts or
   completions or use them for training, and that model providers have no access to them
   (<https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html>), plus any
   model- or provider-specific terms. **If the position for this model is unclear or
   inconclusive, stop: it is a release blocker.**
5. **Invocation logging off.** It would store prompts and completions.
   ```bash
   aws bedrock get-model-invocation-logging-configuration --region ap-southeast-2
   # must return no loggingConfig, or no cloudWatchConfig/s3Config. To disable:
   aws bedrock delete-model-invocation-logging-configuration --region ap-southeast-2
   ```
6. **Add the policy entry** to `APPROVED_SELECTIONS` in `backend/app/ai/policy.py`
   (an entry that names a non-Australian region, lacks streaming/tool capability or has
   incomplete or future-dated evidence cannot be constructed). Set
   `DEFAULT_BEDROCK_CONFIG` (or the `BEDROCK_*` variables).
7. **IAM.** Give the **application task role** streaming invocation only (that is all
   ConverseStream needs: no `bedrock:InvokeModel`, no wildcards), scoped to the approved
   selection. For an inference profile, allow the profile in the client region and the
   underlying foundation model in **each approved Australian destination**, reachable only
   through that profile:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "InvokeApprovedProfile",
         "Effect": "Allow",
         "Action": "bedrock:InvokeModelWithResponseStream",
         "Resource": "arn:aws:bedrock:<client-region>:<account-id>:inference-profile/<profile-id>"
       },
       {
         "Sid": "InvokeUnderlyingModels",
         "Effect": "Allow",
         "Action": "bedrock:InvokeModelWithResponseStream",
         "Resource": [
           "arn:aws:bedrock:ap-southeast-2::foundation-model/<base-model-id>",
           "arn:aws:bedrock:ap-southeast-4::foundation-model/<base-model-id>"
         ],
         "Condition": {
           "StringEquals": {
             "bedrock:InferenceProfileArn": "arn:aws:bedrock:<client-region>:<account-id>:inference-profile/<profile-id>"
           }
         }
       }
     ]
   }
   ```
   For a direct (non-profile) model id, a single statement on
   `arn:aws:bedrock:<client-region>::foundation-model/<model-id>` is enough. List only the
   destinations from your evidence. Confirm the real invocation in step 8 succeeds and
   adjust only as narrowly as that evidence requires.
8. **Real smoke test from the private task** (uses the workload role, the private
   subnets and the Bedrock VPC endpoint from feature 2):
   ```bash
   # via ECS Exec, or a one-off task with this command override
   python -m app.ai.smoke
   ```
   Prints pass/fail flags and codes only (never prompts, completions or tool input) and
   exits 0 only if both a streaming response and a validated structured tool call
   succeeded. With a verification role (control-plane permissions) run
   `python -m app.ai.smoke --verify` to also re-check destinations, streaming support and
   that invocation logging is still disabled. The workload role does not need
   those control-plane permissions.
9. Record the result and date in the register. Features may depend on the adapter only
   after steps 1-8 are complete.

## Approved selections register

| Model / profile id | Kind | Client regions | All inference destinations | Capabilities | Privacy evidence | Verified on / by | Smoke test from private task |
| --- | --- | --- | --- | --- | --- | --- | --- |
| _none: BLOCKED_ | | | | | | | |

Evidence is stored in the code entry (`Evidence`: date, verifier, regional, privacy,
capability notes, sources) and summarised here. The register and `APPROVED_SELECTIONS`
must agree.

## Changing the model or profile

*Moving from the small test model to the production model is this procedure.* Nothing in the
application depends on the model's size: it extracts canvas fields and may propose the next
question, and every proposal is checked (a model question is shown only if it passes the
one-question checker, otherwise the reviewed template is used), so a stronger model changes quality,
not the rules. What it needs is the evidence below for the exact production identifier, and nothing
can be skipped by configuration. (The per-field question wording is in `prompts.py`, not the model.)

Changing an identifier is **not** only a config edit. For every change of model,
inference profile, or the destinations behind a profile (AWS can change these):

1. Repeat steps 1-5 above for the new identifier (regions, capabilities, privacy,
   logging).
2. Add or update its `ApprovedSelection` (limits and allowed provider fields are
   per-model).
3. Apply the narrowly scoped IAM update (step 7).
4. Re-run the smoke test from the private task (step 8) and update the register.

Until then the new identifier is rejected with `unapproved_model`. Run
`--verify`/the live verification periodically: if a profile's destinations drift from the
recorded evidence, the check fails.

## Failure handling

Provider failures never raise out of the adapter. They become content-free `Failure`
values: `code`, `retryable`, `partial_output`, HTTP status and the AWS request id. The
provider's message text is never read, because it can echo request content.

| Code | Typical cause / prerequisite |
| --- | --- |
| `model_not_found` | Identifier not valid in this Region, or model access/approval not granted to the account. |
| `access_denied` | Task-role IAM missing for the profile or an underlying model; SCP blocking an Australian destination; expired credentials. |
| `invalid_request` | The model rejected a setting or message. Fix the configuration or approval limits; it is not retried. |
| `throttled`, `timeout`, `service_unavailable`, `internal_error`, `connection_error` | Transient. `retryable` is true only when no output was delivered. |
| `stream_interrupted`, `stream_error` | Stream ended or failed mid-way. Result is `INCOMPLETE`; never commit or replay. |
| `output_truncated`, `malformed_model_output`, `content_blocked`, `invalid_tool_input`, `missing_tool_call`, `unknown_stop_reason` | The turn finished but is not trustworthy. `INCOMPLETE`; not authoritative canvas content. |

Retries: botocore retries establishing the request up to `max_attempts` with adaptive
backoff. A response that has started streaming is never replayed.

## Privacy in code

* Credentials come from the workload role's default credential chain, never from config.
* Application logs for this module carry model id, status, stop reason, failure code,
  token counts and latency. No messages, system text, completions or tool input.
* `boto3`, `botocore` and `urllib3` loggers are held at WARNING regardless of `LOG_LEVEL`,
  because their DEBUG output includes request parameters.
* Rejected tool input is reported by reason code only and is not retained.
* Tests use synthetic content and stubbed SDK calls; the two synthetic models in the test
  fixtures are not approvals.
