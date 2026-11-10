# "Privacy proven" canary check (runbook)

Shows that content a user typed leaves no trace once the session ends, by each of the three
routes: the user purges it, the user finishes and signs out, or they walk away and the sweeper
removes it. It needs a deployed environment and real AWS, so it cannot run in CI. Run it on
`test` before the first prod release and after any change to storage, logging or expiry.

You run the `aws` commands yourself with your own profile; none of this is automated.

```bash
export AWS_REGION=ap-southeast-2 ENV=test            # or prod
export TABLE=plan-$ENV-sessions
export WEB_LOGS=/plan/$ENV/web SWEEPER_LOGS=/plan/$ENV/sweeper
for s in A B C; do export CANARY_$s="CANARY-$s-$(uuidgen | tr -d - | tr A-Z a-z)"; done
echo "$CANARY_A $CANARY_B $CANARY_C"                  # note them down
count() { aws dynamodb scan --table-name "$TABLE" --output json | grep -c "$1" || true; }
```

`count` streams the scan through `grep`; nothing is written to disk. The table is small
(roughly 50 users, 24 hour life), so a scan is fine here.

## 1. Seed three sessions

In a browser, enter the access code and start **three separate canvases** (private windows).
In each, answer the first interview question with text containing that session's canary, for
example `Our idea is <CANARY_A>`. In session A also attach a small `.docx` or `.pdf` whose body
contains its canary. Then:

| Session | What you do next |
| --- | --- |
| A, purged | Download both PDFs, then **Delete session** (purge). |
| B, signed out | Download both PDFs, then sign out. |
| C, abandoned | Close the window. Do nothing else. |

Check the PDFs from A and B contain their canary: that is the export working, and the only
place the canary should survive.

## 2. Positive control

The search only means something if it can find a canary that is there. While C still exists:

```bash
count "$CANARY_C"          # expect 1 or more
```

If this is 0, the check is broken (wrong table, missing permission, or the content is stored
somewhere this scan does not see). Stop and find out why before trusting any zero below.

## 3. After A and B have finished

```bash
count "$CANARY_A"          # expect 0  (purged)
count "$CANARY_B"          # expect 0  (signed out)
count "$CANARY_C"          # expect 1 or more (still within 24 hours)
```

## 4. Let C expire, then sweep

A session is deletable once it is more than 24 hours old, or has had no activity for 15 minutes.
After that, wait for the 5 minute
schedule or run the sweeper once by hand:

```bash
out() { aws cloudformation describe-stacks --stack-name plan-$ENV-service \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
aws ecs run-task --cluster plan-$ENV --launch-type FARGATE --task-definition plan-$ENV-sweeper \
  --network-configuration "awsvpcConfiguration={subnets=[$(out TaskSubnetIds)],securityGroups=[$(out TaskSecurityGroupId)],assignPublicIp=$(out TaskAssignPublicIp)}"
```

Wait for the task to stop. Its exit code must be 0, and the log must show
`expiry_run ... failed=0`:

```bash
aws logs tail "$SWEEPER_LOGS" --since 30m | grep expiry_run
```

## 5. Search everywhere. Expect zero

```bash
for c in "$CANARY_A" "$CANARY_B" "$CANARY_C"; do count "$c"; done      # expect 0 0 0

# CloudWatch Logs, web and sweeper, over the whole test period (the last two days here).
for g in "$WEB_LOGS" "$SWEEPER_LOGS"; do
  for c in "$CANARY_A" "$CANARY_B" "$CANARY_C"; do
    aws logs filter-log-events --log-group-name "$g" --filter-pattern "\"$c\"" \
      --start-time $(( ($(date +%s) - 172800) * 1000 )) --query 'length(events)'    # expect 0
  done
done
```

Positive control for the logs: the same `filter-log-events` with `--filter-pattern expiry_run`
on the sweeper group must return events, or the zeros above mean nothing.

Finally check the table has no items left over from the three sessions: if it was empty before
you started, `aws dynamodb scan --table-name "$TABLE" --select COUNT` must return `0`.

## Result

Record the date, environment, the git SHA of the running image, the three canaries and the
output of every command above. **Privacy is proven for that build only when every "expect 0" is
0 and every positive control was above 0.**

## After the test: nothing left in Parameter Store

The access code hash and cookie key are not session content, so the canary does not touch them. With
the `parameterstore` backend they are SSM parameters that `cdk destroy` does not remove (the Destroy
workflow does). After destroying the environment, confirm:

```bash
aws ssm get-parameters --names /plan-$ENV/access-code-hash /plan-$ENV/cookie-signing-key \
  --query 'Parameters[].Name'          # expect []
```

## What this does not cover

* The DynamoDB TTL backstop (it can lag by days; the sweeper is the primary control).
* The browser's own copy: downloaded PDFs and anything the user saved.
* Account-level logging such as CloudTrail, which records who called what, not content.
* Bedrock: invocation logging must be off (`docs/bedrock.md`). Check it separately with
  `aws bedrock get-model-invocation-logging-configuration`.
