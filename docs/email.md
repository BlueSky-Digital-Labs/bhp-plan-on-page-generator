# Emailing the canvas and appendix

On the review page, once the canvas is confirmed ([appendix.md](appendix.md)), a **Send email**
button opens a small form: who to send to, and an optional message. When the user sends, the
**server** builds the one-page canvas and the appendix as PDFs for the confirmed revision and
emails both as attachments through Amazon SES. Nothing is stored: not the PDFs, the addresses or
the message. Code: `backend/app/mailer/` (sending), `backend/app/api/mail.py` (the route),
`frontend/src/components/EmailPanel.tsx` (the form).

Email is **off until configured** (`EMAIL_DELIVERY` unset). Until then the form says "Email is not
switched on yet" and nothing is built or sent.

## The route

`POST /api/email` with `{"revision": 3, "to": ["a@example.com"], "message": "optional text"}`
(session cookie and CSRF token required, like every other change). Answers `{"sent": <count>}`.

It sends only if the revision is **the current one, complete and confirmed**, the same gate as the
downloads, checked again after the PDFs are built and just before the send. The PDFs are the same
files the download buttons give (`-canvas-r<rev>.pdf`, `-appendix-r<rev>.pdf`), including the AI
overview in the appendix when there is one.

| Status | `error.code` | When |
| --- | --- | --- |
| 422 | `invalid_recipient` / `invalid_request` | An address is not a plain address, none given, more than 5, or the message is over 2,000 characters |
| 422 | `recipient_not_allowed` | The domain is not on the allow-list |
| 503 | `email_unavailable` | Email is not configured (or the per-session table is full) |
| 409 | `not_confirmed`, `revision_conflict`, `canvas_not_ready`, `turn_in_progress`, `conversation_changed` | The canvas is not complete and confirmed for that revision |
| 409 | `send_in_progress` | An email from this session is already being sent |
| 429 | `email_limit` | The session has used its 5 emails |
| 502 | `email_rejected` | SES refused it (for example a recipient that is not verified while the account is in the SES sandbox) |
| 502 / 503 / 413 | `email_failed` / `email_busy` / `email_too_large` | SES failed, is throttling, or the two PDFs together are over 10 MB |
| 500 | `appendix_failed` | A PDF could not be built |

Error messages are fixed text. Addresses and the message are never echoed back or logged; the log
line is `email_sent` with only the recipient count (or `email_send_failed` with the SES error code).

## What the email looks like

* **From:** the one configured sender address. The user cannot choose it, and there is no Reply-To,
  Cc or Bcc.
* **To:** the recipients the user typed (1 to 5, plain `name@domain.tld` addresses only).
* **Subject:** `Plan on a Page: <solution name>` (the name is flattened to one line and cut at 80
  characters, so a name can never add headers).
* **Body:** plain text: a line naming the canvas, the two file names, then the user's message under
  "Message from the sender:" (control characters removed, never treated as markup), then
  "Sent from the Plan on a Page app."
* **Attachments:** the two PDFs.

## Safeguards

* **Allow-listed recipient domains.** `EMAIL_ALLOWED_DOMAINS` is required with `ses`; the app starts
  only with it. An address on any other domain is refused, so the app cannot be used to email
  arbitrary outsiders from the company's address.
* **Strict addresses.** Plain ASCII only: no display names, quotes, angle brackets, commas or line
  breaks, so an address cannot inject headers or a second recipient.
* **Five emails per session, one at a time.** A second click while a send is running gets
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
| `EMAIL_ALLOWED_DOMAINS` | Comma-separated recipient domains, for example `bhp.com,example.com`. Required with `ses` |
| `SES_REGION` | The SES region, if not the app's own (`AWS_REGION`, default `ap-southeast-2`) |
| `EMAIL_CONFIGURATION_SET` | Optional SES configuration set (for delivery events) |

A bad value stops startup with an error that names the setting but not its value.

### Turning on SES

1. In the SES console (the region above), verify the sender **identity** (the address, or its
   domain with DKIM). SES refuses to send from anything else.
2. A new SES account is in the **sandbox**: it can send only to verified addresses and at a low
   rate. Verify the recipients you are testing with, or request production access before real use.
3. Give the task role permission to send, scoped to the identity and the sender address:

   ```json
   {
     "Effect": "Allow",
     "Action": "ses:SendEmail",
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
   export SES_REGION=ap-southeast-2
   APP_ENV=local uv run uvicorn --factory app.main:create_app --port 8000
   ```

## Not done in this version

* **Not run against real SES.** The SES calls are tested with stubbed SDK responses (request shape
  and every error mapped) and nothing has been sent through an account. Send a test email to a
  verified address before relying on it.
* **No delivery status.** "Sent" means SES accepted the message, not that it reached the inbox.
  Bounces and complaints need an SES configuration set with an event destination.
* **No audit record of who was emailed.** Logs are content-free by design. If BHP needs a record
  of recipients, that is a separate decision (what to keep, where, for how long).
* **Per-task send counts** (see above) until there is a durable session store.
* **Sender identity is the app, not the person.** Access is by shared code, so the email says
  "Sent from the Plan on a Page app" and carries no name of the user who sent it.
