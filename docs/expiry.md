# Automatic expiry (24 hours)

Every session ends 24 hours after it began (`SESSION_LIFETIME`, fixed at creation and never
extended). This is the privacy safety net for people who leave without deleting: a scheduled
command removes every session that is **strictly more than 24 hours old**, with its conversation,
canvas draft, delivery records and cached overview, whether or not anything was downloaded.

Code: `backend/app/expiry.py`. Tests: `backend/tests/test_expiry.py`.

## The command

```bash
cd backend && uv run python -m app.expiry      # in the container: python -m app.expiry
```

One run is one sweep, then it exits: `0` if every expired session was removed, `1` if any could not
be (or if there is no store to sweep). A failed scheduled task therefore shows up as a failed task.

For each expired session it:

1. ends the sign-in record first, so nothing more can be written under it,
2. deletes the content with the same routine sign-out and purge use (`delete_session`),
3. carries on if one session fails; that one stays listed and the next run retries it.

Sessions are found from the content store's own list of ids and expiry times
(`expired_session_ids(now, limit, after)`), read a page at a time (100 per page, at most 1,000
pages per run; a run that stops at the limit says `complete=False` and the next run continues). It
does not depend on the sign-in record, which TTL may already have removed. Repeating a sweep, or
running two at once, is harmless: every step is idempotent.

The download gate is bypassed on purpose: the user may never come back.

The log line is `expiry_run` with counts only (`found=… deleted=… failed=… complete=…`), or
`expiry_delete_failed` (exception type only) and `expiry_unavailable`. No ids, content or addresses.

## 24 hours versus 15 minutes

The **threshold** is 24 hours. The **schedule** (every 15 minutes) only decides how soon after the
threshold the data is physically removed. A session that began at 09:00 becomes deletable at 09:00
the next day and is removed by the first sweep after that: up to about 24 hours 15 minutes after it
began. The session cookie stops working at 24 hours regardless. DynamoDB TTL is only a backstop and
can lag by up to about two days, which is why the application checks expiry itself.

## What runs it (not in this repository)

This repository has the command, not the infrastructure. In AWS it needs:

* an **EventBridge schedule** `rate(15 minutes)` launching an **ECS task** from the application
  image with the command overridden to `python -m app.expiry`, in the private Sydney runtime;
* its **own task role**, separate from the web service: read and delete on the session and
  conversation tables (including the index), the KMS permissions for their encryption key, and
  CloudWatch Logs; nothing else, and no Bedrock or SES;
* DynamoDB **TTL** on the `expires_at` attribute as the supplementary safeguard;
* an alarm on the task **stopping with a non-zero exit code** (an EventBridge rule on the ECS task
  state change), following the existing operational alarm conventions.

## Verifying it

Locally the in-memory stores are empty in a separate process, so the command only proves it starts:

```bash
APP_ENV=local uv run python -m app.expiry   # expect: expiry_run  found=0 deleted=0 failed=0 complete=True, exit 0
```

The sweep logic is verified by `tests/test_expiry.py` with a controlled clock: the exact 24-hour
boundary, paging and the page limit, repeats and overlapping sweeps, one session failing while the
rest are removed and then retried, the sign-in record ended before content is deleted, abandoned
sessions with no download, and the command's exit codes.

Once the durable stores exist, verify the schedule in AWS: run the task once by hand with the command
override, check that CloudWatch shows `expiry_run` and the task exit code is 0, create a test session,
wait until it is past 24 hours (or use a test environment with a shortened lifetime), and check that
the next run reports it as deleted and its records are gone. Then force a failure (for example a
revoked permission) and confirm the alarm fires.

## Not done

* **Not runnable against real storage.** Deployed, the command exits `1` with `expiry_unavailable`
  because the conversation store has no durable version yet ([downloads.md](downloads.md)).
* **No infrastructure, schedule, role or alarm** here. Those need to be created in the infrastructure
  code; the list above is the specification.
* **Sessions with no content** (signed in, never wrote anything) are removed by their own record's
  TTL; the sweep only lists sessions that have content.
