# Submitting the canvas and appendix by email

On the review page, once the canvas is confirmed ([appendix.md](appendix.md)), a **Submit by email**
button sends the one-page canvas and the appendix, as two PDF attachments, to **one fixed address**
with **fixed text**. There is no form: the user cannot type an address or a message, and the
browser sends only the revision. The **server** builds both PDFs for the confirmed revision and
emails them through Amazon SES. Nothing is stored: not the PDFs and not the recipient. Code:
`backend/app/mailer/` (sending and settings), `backend/app/api/mail.py` (the route),
`frontend/src/hooks/useSubmitByEmail.ts` and `frontend/src/components/ReviewScreen.tsx` (the button).

Email is **off until configured** (`EMAIL_DELIVERY` unset, or no recipient). Until then the button
says "Submitting by email is not switched on yet" and nothing is built or sent.

## Changing the recipient or the text without a deploy

An environment variable on a running container cannot change without restarting it, so in AWS the
recipient and text are **Parameter Store strings** that the app re-reads at least once a minute:

| Parameter | Holds | If missing |
| --- | --- | --- |
| `/plan-<env>/email-recipient` | The one address submissions go to, for example `team@bhp.com` | Submissions are unavailable |
| `/plan-<env>/email-body` | The whole text of the email (up to 500 characters). Optional | The built-in text, below |

The built-in text is: "SCP AI Ideation assistant submits a one page canvas for job development and
with the 2 attachments".

To change either, edit the parameter's value in the AWS console (Systems Manager, Parameter Store,
the parameter, Edit) or run `aws ssm put-parameter --name /plan-test/email-recipient --value
team@bhp.com --type String --overwrite`. The running app uses the new value within about a minute:
no deploy, no restart. To create a parameter the first time, add the organisation's tags
(`infra/org-tags.json`), which the console asks for under Tags; AWS does not allow tags together
with `--overwrite`, so tag when creating. The deploy gives the web role read access to exactly these
two parameters and nothing else (`ssm:GetParameters`); the stacks do not create them and Destroy
removes them.

The recipient is **checked on every read**: it must be one plain address on one of
`EMAIL_ALLOWED_DOMAINS`. A mistyped or disallowed value stops submissions (log `email_recipient_invalid`)
instead of sending to it, and a fix restores them within a minute. If Parameter Store cannot be
read, the last good value keeps being used; with none yet, submissions are unavailable
(`email_settings_unavailable`). Editing needs `ssm:PutParameter` on those two names, so whoever may
change the recipient is controlled in IAM, not in the app.

## The route

`POST /api/email` with `{"revision": 3}` (session cookie and CSRF token required, like every other
change; any other field is refused). Answers `{"sent": 1}`.

It sends only if the revision is **the current one, complete and confirmed**, checked again after
the PDFs are built and just before the send. (Downloads are looser: an unfinished draft can be saved,
see [downloads.md](downloads.md). Email never goes out for a draft.) The PDFs are the same
files the download buttons give (`-canvas-r<rev>.pdf`, `-appendix-r<rev>.pdf`), including the AI
overview in the appendix when there is one.

| Status | `error.code` | When |
| --- | --- | --- |
| 422 | `invalid_request` | Anything other than the revision was sent (a recipient, text, cc or sender) |
| 503 | `email_unavailable` | Email is not configured, there is no valid recipient, or the per-session table is full |
| 409 | `not_confirmed`, `revision_conflict`, `canvas_not_ready`, `turn_in_progress`, `conversation_changed` | The canvas is not complete and confirmed for that revision |
| 409 | `send_in_progress` | A submission from this session is already being sent |
| 429 | `email_limit` | The session has used its 5 submissions |
| 502 | `email_rejected` | SES refused it (for example the recipient is not verified while the account is in the SES sandbox) |
| 502 / 503 / 413 | `email_failed` / `email_busy` / `email_too_large` | SES failed, is throttling, or the two PDFs together are over 10 MB |
| 500 | `appendix_failed` | A PDF could not be built |

Error messages are fixed text. The address is never echoed back or logged; the log line is
`email_sent` (or `email_send_failed` with the SES error code).

## What the email looks like

* **From:** the one configured sender address. The user cannot choose it, and there is no Reply-To,
  Cc or Bcc.
* **To:** the one configured recipient.
* **Subject:** `<app name>: <solution name>` (the name is flattened to one line and cut at 80
  characters, so a name can never add headers). Set `APP_NAME` to change the first part.
* **Body:** exactly the configured text, as plain text (control characters removed, never treated
  as markup).
* **Attachments:** the two PDFs.

## Safeguards

* **One recipient, set by the operator.** The browser can neither choose nor add an address or
  text, so the feature cannot be used to email anyone else even by calling the API directly.
* **Allow-listed recipient domain.** `EMAIL_ALLOWED_DOMAINS` is required with `ses`, and the
  configured recipient must be on it, so a wrong edit cannot send outside the company.
* **Five submissions per session, one at a time.** A second click while a send is running gets
  `send_in_progress`; only emails that SES accepted count against the five. The count lives in
  memory and ends with the session, so behind several tasks it is per task. The SES account quota
  and the WAF rate rule are the shared backstops.
* **No automatic retry.** The SES client makes one attempt, because SES does not de-duplicate and a
  retry after a lost reply could deliver the same email twice. A timeout is reported as a failure.
* **Same login and forgery protections** as the other change routes (cookie session, CSRF token,
  Origin check). One session cannot send another session's canvas.

## Configuration

| Variable | Meaning |
| --- | --- |
| `EMAIL_DELIVERY` | `unavailable` (default: nothing is sent), `ses`, or `test` (accepts and discards; presentation tests only, refused when deployed) |
| `EMAIL_SENDER_ADDRESS` | The verified SES sender, a plain address. Required with `ses` |
| `EMAIL_ALLOWED_DOMAINS` | Comma-separated domains the recipient must be on, for example `bhp.com`. Required with `ses` |
| `EMAIL_RECIPIENT_PARAMETER` | Name of the Parameter Store string holding the recipient. Set by the deploy to `/plan-<env>/email-recipient`. With `ses`, this or `EMAIL_RECIPIENT` is required |
| `EMAIL_BODY_PARAMETER` | Name of the string holding the text (optional). Set by the deploy to `/plan-<env>/email-body` |
| `EMAIL_RECIPIENT`, `EMAIL_BODY` | Fixed values, for local use. Used when no parameter name is set. The recipient is checked against the allowed domains at start-up |
| `SES_REGION` | The SES region, if not the app's own (`AWS_REGION`, default `ap-southeast-2`) |
| `EMAIL_CONFIGURATION_SET` | Optional SES configuration set (for delivery events) |

A bad value stops startup with an error that names the setting but not its value.

### Turning on SES

1. In the SES console (the region above), verify the sender **identity** (the address, or its
   domain with DKIM). SES refuses to send from anything else.
2. A new SES account is in the **sandbox**: it can send only to verified addresses and at a low
   rate. Verify the recipients you are testing with, or request production access before real use.
3. Give the task role permission to send, scoped to the identity and the sender address. Both
   actions are needed: the app sends a raw message so the PDFs can be attached, and SES
   authorises that as `ses:SendRawEmail` (leaving it out gives `AccessDeniedException`):

   ```json
   {
     "Effect": "Allow",
     "Action": ["ses:SendEmail", "ses:SendRawEmail"],
     "Resource": "arn:aws:ses:<region>:<account>:identity/<sender-or-domain>",
     "Condition": { "StringEquals": { "ses:FromAddress": "<EMAIL_SENDER_ADDRESS>" } }
   }
   ```

4. Set the variables and restart the backend:

   ```bash
   export AWS_PROFILE=<profile>                 # or the task role in AWS
   export EMAIL_DELIVERY=ses
   export EMAIL_SENDER_ADDRESS=noreply@your-domain.example
   export EMAIL_ALLOWED_DOMAINS=your-domain.example
   export EMAIL_RECIPIENT=team@your-domain.example     # in AWS this is EMAIL_RECIPIENT_PARAMETER
   export SES_REGION=ap-southeast-2
   APP_ENV=local uv run uvicorn --factory app.main:create_app --port 8000
   ```

## Not done in this version

* **Parameter Store read is tested with a stub.** The settings source, its one-minute cache and its
  failure handling are tested against a fake client, not a real parameter. After the first deploy,
  create the recipient parameter, submit once, then edit it and check the next submission follows.
* **No delivery status.** "Sent" means SES accepted the message, not that it reached the inbox.
  Bounces and complaints need an SES configuration set with an event destination.
* **No audit record of who was emailed.** Logs are content-free by design. If BHP needs a record
  of recipients, that is a separate decision (what to keep, where, for how long).
* **Per-task send counts** (see above) until there is a durable session store.
* **Sender identity is the app, not the person.** Access is by shared code, so the email carries
  no name of the user who submitted it.
* **A submission can be repeated.** Pressing the button again sends the same documents again (up to
  5 times a session). There is no "already submitted" lock, because the user may have edited and
  re-confirmed in between.
