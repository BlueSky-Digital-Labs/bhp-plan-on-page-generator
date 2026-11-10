# Automatic expiry (15 minutes idle, 24 hours at most)

A session ends at whichever comes first:

* **15 minutes without activity** (`SESSION_IDLE`): the session stops working at once and its
  data is deleted, so someone who gets distracted and leaves does not leave their idea on the server;
* **24 hours after it began** (`SESSION_LIFETIME`, fixed at creation, never extended by activity).

Activity is the person being at the page, not only what reaches the server:

* actions the server sees: sending an answer, attaching a file, confirming, downloading, submitting
  by email, or **Keep my session** on the warning;
* real use of the page: typing (a long answer is not idle), clicking, scrolling or moving the mouse.
  The page reports this to the server at most once a minute (`POST /api/session/keepalive`).

Loading or polling the page (`GET /api/session`, `/api/conversation`, `/api/downloads`) does **not**
count, so a tab left open and untouched never keeps a session alive. The 15 minutes count from the
last activity, not from when the session began, and each activity restarts them; none moves the 24
hours.

How it is enforced:

* every request is checked against both limits on the server; a session idle for 15 minutes is
  rejected, its sign-in record is deleted and, in the same request, so is its content;
* the content's expiry date (the one the scheduled sweep reads) slides to "15 minutes after the last
  activity, but never later than the 24 hour end" (`ConversationService.touch`), so sessions nobody
  comes back to are removed by the sweep. It runs **every 5 minutes**, so the data of an idle session
  is gone within about 20 minutes of the last activity (15 idle plus up to 5 for the sweep);
* the page shows a warning two minutes before the end, with a **Keep my session** button, and says
  plainly on the entry page if the session ended for being idle.

This is the privacy safety net for people who leave without deleting: the scheduled command removes
every session past either limit, with its conversation, canvas draft, delivery records and cached
overview, whether or not anything was downloaded.

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

## The two limits and the schedule

The **limits** are 15 idle minutes and 24 hours. The **schedule** (every 5 minutes) only decides how
soon after a limit the data is physically removed. A session that began at 09:00 and was last active
at 09:10 stops working at 09:25 and is removed by the first sweep after that (by 09:30). A session
that is kept active still ends at 09:00 the next day. DynamoDB TTL is only a backstop and can lag by
up to about two days, which is why the application checks expiry itself.

## What runs it (not in this repository)

This repository has the command, not the infrastructure. In AWS it needs:

* an **EventBridge schedule** `rate(5 minutes)` launching an **ECS task** from the application
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

Verify the schedule in AWS: run the task once by hand with the command
override, check that CloudWatch shows `expiry_run` and the task exit code is 0, create a test session,
wait until it is past 24 hours (or use a test environment with a shortened lifetime), and check that
the next run reports it as deleted and its records are gone. Then force a failure (for example a
revoked permission) and confirm the alarm fires.

## Not done

* **Not run against real DynamoDB.** The sweep is tested against the DynamoDB stores on `moto`
  (including a table scan after a sweep that finds nothing left, and a session whose sign-in record is
  already gone); run it once against the real table ([deployment.md](deployment.md)).
* **No infrastructure, schedule, role or alarm** here. Those need to be created in the infrastructure
  code; the list above is the specification.
* **Sessions with no content** (signed in, never wrote anything) are removed by their own record's
  TTL; the sweep only lists sessions that have content.
