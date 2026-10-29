# LocalGrowth AI backend

Minimal FastAPI service backed by SQLAlchemy and SQLite. Durable jobs use simulation by default. A separately enabled controlled test can dispatch one selected purchase's job through the Meta test sender.

## Launch locally (PowerShell)

Run these commands from the repository root:

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/init_local.py
uvicorn app.main:app --reload --no-access-log
```

`init_local.py` creates `backend/.env` with persistent phone-encryption and lookup secrets. Keep that file private and do not regenerate its keys after storing data. The SQLite database is `backend/localgrowth.db`; both it and `.env` are ignored by Git. Change `THANK_YOU_DELAY_SECONDS` in `.env` to a short value (for example, `2`) when demonstrating the seven-minute thank-you delay.

Promotional campaigns require a recorded promotional opt-in. A recent promotional job suppresses another campaign preview for that customer for `PROMOTIONAL_SUPPRESSION_HOURS` (168 hours by default); setting the promotional permission to false on a purchase records an opt-out. Thank-you permission remains independent.

Open `http://127.0.0.1:8000/docs` for the FastAPI schema. For a fictional sample customer and purchase, stop the server and run `python -m app.seed_demo` from `backend/` after local initialization.

## WhatsApp Cloud API test

The standalone CLI sends one Meta-approved template and does not change purchase automation or simulation mode. In local `backend/.env`, set `WHATSAPP_MODE=test`, the existing Meta access token, phone number ID, and Graph API version, then set `WHATSAPP_TEST_RECIPIENT` to your own verified E.164 number. Add that same number to `WHATSAPP_TEST_ALLOWED_RECIPIENTS` (comma-separated allowlist) and set `WHATSAPP_TEST_RECIPIENT_VERIFIED=true` only after verifying it in Meta's developer dashboard. Copy the exact approved template name and language code from WhatsApp Manager. Set `WHATSAPP_TEST_TEMPLATE_COMPONENTS` to a JSON array matching the template's variables exactly; use `[]` for a template with no parameters. Do not put customer data in test parameters.

From `backend/`, first preview without sending, then explicitly send one message:

```powershell
.\.venv\Scripts\python.exe .\scripts\send_whatsapp_test.py
.\.venv\Scripts\python.exe .\scripts\send_whatsapp_test.py --send
```

The CLI prints only a generic result and Meta's message ID; it never prints the recipient or access token. The sender uses Meta's versioned Graph API messages endpoint and approved template payload format [from Meta's WhatsApp Cloud API collection](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api?entity=request-13382743-d0e227d1-e520-4b12-a8f6-32657a52d26e).

## Controlled automatic WhatsApp test

Automatic real sending defaults to disabled. Three additional settings select one controlled purchase:

| Setting | Default | Meaning |
| --- | --- | --- |
| `WHATSAPP_AUTOMATION_MODE` | `simulation` | Must be `real_test` for the controlled test. |
| `WHATSAPP_AUTOMATION_ENABLED` | `false` | Must be explicitly `true` at creation and dispatch. |
| `WHATSAPP_AUTOMATION_PURCHASE_KEY` | empty | Only a new purchase with this exact idempotency key may create a real-test job. |

The existing `WHATSAPP_MODE=test`, verified recipient, allowlist, Graph credentials, and approved template settings remain required. Use the exact approved template name, language and components from Meta with fictional test parameter values. An order-confirmation template is a connectivity test only; it is not a production thank-you template. Real-test jobs expose `channel=whatsapp_real_test`, `delivery_mode=real_test`, and an explicit `test_notice`. The helper explicitly requests `delivery_mode=real_test`; the server rejects that request before creating a purchase if its own opt-in, selected key, consent, or recipient settings do not match. Ordinary requests retain their existing simulation behavior.

All existing jobs and other purchases continue simulating. The worker never decrypts or sends to arbitrary customer phone numbers: it compares the selected customer's complete lookup hash to the verified, allowlisted test recipient. It rechecks consent, the switches, the purchase key, and recipient eligibility before dispatch. A switch disabled after queuing causes the real-test job to be skipped, not sent or silently simulated. `THANK_YOU_DELAY_SECONDS` still defaults to 420 seconds and may be shortened in the server terminal for a demo.

`app/migrate.py` is an additive, repeatable SQLite migration. It adds job metadata, a unique attempt ledger, and a transition guard that prevents a stale simulation-only worker from overwriting a real-test job. Existing jobs receive `delivery_mode=simulation` only when that column does not already exist; existing job modes, customer rows, ciphertext and secrets are preserved. Startup also runs this migration. Stop all previous backend workers before upgrading; do not regenerate `.env`, keys or the database.

The worker commits a `processing` reservation and one permanent attempt record per purchase before HTTP. Concurrent workers and duplicate job rows cannot create another provider attempt for that purchase. There is no automatic retry, including after network errors, ambiguous timeouts, or a crash before/after the HTTP request. This deliberately trades possible missed test messages for duplicate prevention; it does not claim exactly-once delivery. Interrupted jobs stay `processing` with `provider_outcome_unconfirmed_no_retry`; failed jobs have sanitized reasons such as `meta_auth_invalid_or_expired` or `meta_timeout_outcome_unknown`. Reconcile uncertain attempts manually before authorizing any new test purchase. Never clear the attempt ledger or reset these jobs to retry.

| Job status | Meaning |
| --- | --- |
| `queued` | Waiting for its existing scheduled time. |
| `processing` | Durable claim recorded; dispatch may be in flight or interrupted. No automatic reclaim. |
| `accepted` | Meta returned a message ID, stored as `provider_message_id`; not proof of delivery. |
| `simulated` | Completed locally without a Meta request. |
| `delivered` | Reserved for authenticated provider delivery confirmation. This phase has no webhook receiver and never assigns this state. |
| `failed` | Provider failure or unknown outcome; inspect `reason`. No automatic retry. |
| `skipped` | Consent, configuration, recipient, or duplicate check prevented dispatch. |

`GET /api/jobs` filters and dashboard counts support all seven states. Jobs also expose `processing_started_at`, `accepted_at`, `delivered_at` and `provider_message_id`. Accepted jobs keep `delivered_at` and `completed_at` null. Delivery tracking requires a later authenticated webhook implementation; [Meta tracks message status by ID via webhooks](https://www.postman.com/meta/whatsapp-business-platform/folder/o48mro7/messages).

### One automatic test (PowerShell, two terminals)

First stop the old backend. Keep the existing verified recipient and approved template settings in your private `.env`. In terminal 1, start the updated server with temporary test settings:

```powershell
Set-Location "C:\Users\AKSHAY KUMAR\localgrowth-ai\backend"
.\.venv\Scripts\python.exe -m app.migrate
$env:WHATSAPP_AUTOMATION_MODE = "real_test"
$env:WHATSAPP_AUTOMATION_ENABLED = "true"
$localgrowthKeyFile = Join-Path ([IO.Path]::GetTempPath()) "localgrowth-wa-test-key.txt"
$env:WHATSAPP_AUTOMATION_PURCHASE_KEY = "wa-fix-" + ([guid]::NewGuid().ToString("N") -replace '(.{4})', '${1}x')
[IO.File]::WriteAllText($localgrowthKeyFile, $env:WHATSAPP_AUTOMATION_PURCHASE_KEY)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

The startup log reports the worker PID, effective automation mode, whether real-test sending is enabled, and whether a test key is configured; it never prints the key, token, or recipient. Confirm it reports `automation_mode=real_test`, `real_test_enabled=True`, and `test_key_configured=True`. Settings are captured by the server process at startup: setting variables in another terminal does not change an already-running Uvicorn process.

In terminal 2, queue a synthetic purchase for the configured verified recipient. The consent flag records your explicit thank-you choice; promotional consent is not changed. This command schedules a real test request after the delay:

```powershell
Set-Location "C:\Users\AKSHAY KUMAR\localgrowth-ai\backend"
$env:WHATSAPP_AUTOMATION_MODE = "real_test"
$env:WHATSAPP_AUTOMATION_ENABLED = "true"
$localgrowthKeyFile = Join-Path ([IO.Path]::GetTempPath()) "localgrowth-wa-test-key.txt"
$env:WHATSAPP_AUTOMATION_PURCHASE_KEY = [IO.File]::ReadAllText($localgrowthKeyFile).Trim()
.\.venv\Scripts\python.exe .\scripts\queue_whatsapp_automation_test.py --queue --consent-thank-you
```

The first terminal generates a fresh key and saves it to a temporary file so both processes select the same purchase. Do not regenerate it on retry: reuse that file if the result is uncertain. The helper prints only the job ID, status and due time. It sends no template itself; the background worker does that when due. After the delay, inspect the job:

```powershell
(Invoke-RestMethod "http://127.0.0.1:8000/api/jobs?job_type=thank_you").data |
    Select-Object job_id, status, scheduled_at, provider_message_id, reason
```

After the test, stop the server, set `WHATSAPP_AUTOMATION_ENABLED=false` and `WHATSAPP_AUTOMATION_MODE=simulation` in terminal 1, and restart it to resume simulation. These shell settings do not change `.env`. Only choose a new purchase key when intentionally authorizing another test after reconciling the previous one.

## Test

```powershell
Set-Location backend
python -m pip install -r requirements.txt
python -m pytest -q
```

## WhatsApp delivery-status webhook

`GET /api/webhooks/whatsapp` answers Meta's verification challenge only when `hub.mode=subscribe` and `hub.verify_token` matches `WHATSAPP_WEBHOOK_VERIFY_TOKEN`. It returns the exact `hub.challenge` as plain text. `POST /api/webhooks/whatsapp` requires a valid `X-Hub-Signature-256` HMAC over the raw body, using `WHATSAPP_APP_SECRET`. Missing or invalid secrets/signatures fail closed. Neither route sends a message. Webhook reception works while outgoing automation remains in simulation mode.

Set these **only in ignored `backend/.env`** before starting the webhook listener:

| Setting | Source |
| --- | --- |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | A new private, random value you choose; enter the same value in Meta's webhook setup. This is separate from the access token and phone-encryption secrets. |
| `WHATSAPP_APP_SECRET` | The App Secret in Meta App Dashboard → App Settings → Basic. Do not use the access token here. |
| `WHATSAPP_PHONE_NUMBER_ID` | The existing test sender's phone number ID; callbacks with another or missing ID are ignored. |

Stop the old backend before starting updated code. Take a consistent backup **before** running the additive migration. Run from `backend/` in PowerShell:

```powershell
.\.venv\Scripts\python.exe .\scripts\backup_sqlite.py
.\.venv\Scripts\python.exe -m app.migrate
$env:WHATSAPP_AUTOMATION_MODE = "simulation"
$env:WHATSAPP_AUTOMATION_ENABLED = "false"
.\.venv\Scripts\python.exe -m uvicorn app.webhook_only:app --host 127.0.0.1 --port 8001 --no-access-log
```

The backup script uses SQLite's online backup API and checks the copy. The backup filename is ignored by Git. `app.webhook_only` has only the two webhook routes; use it for the public tunnel. Keep the normal API on localhost port 8000 for local use, also in simulation mode. Both processes use the same SQLite database. Restart both processes after editing `.env` because configuration is loaded at startup.

In another PowerShell terminal, verify both routes locally without a Meta message or database update:

```powershell
Set-Location "C:\Users\AKSHAY KUMAR\localgrowth-ai\backend"
.\.venv\Scripts\python.exe .\scripts\check_whatsapp_webhook.py
```

For a temporary public HTTPS callback, [install `cloudflared` and create a Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) in a separate terminal:

```powershell
cloudflared tunnel --url http://127.0.0.1:8001
```

Use the printed `https://<temporary-host>.trycloudflare.com/api/webhooks/whatsapp` as Meta's Callback URL. This tunnel reaches only the webhook-only listener, not the purchase or customer API. Optionally verify the public route with `python .\scripts\check_whatsapp_webhook.py --url "https://<temporary-host>.trycloudflare.com/api/webhooks/whatsapp"`; the script prints no token or phone number and posts only an empty signed test payload. Keep request access logs disabled. Quick Tunnel URLs change when restarted and are for development only.

In Meta's WhatsApp webhook setup, select the **whatsapp_business_account** object, enter the callback URL and the same verify token, complete verification, and subscribe to the **messages** field for the test WhatsApp Business Account. Meta's [official Cloud API collection](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api?entity=request-13382743-d0e227d1-e520-4b12-a8f6-32657a52d26e) documents WABA subscription and status callback shapes. Check the verified callback in Meta before considering another live send.

The receiver matches `statuses[].id` to the exact `provider_message_id` of one `real_test` thank-you job. It records `delivery_status` (`sent`, `delivered`, `read`, or `failed`), `delivery_event_at`, and a numeric `delivery_failure_code` when Meta supplies one. It never stores Meta's raw error text, recipient number, or inbound message body. `sent` keeps the job `accepted`; `delivered` and `read` set its existing job status to `delivered`; `failed` sets it to `failed` with reason `meta_delivery_failed`. Repeated, lower-priority, and older-timestamp callbacks cannot regress a confirmed status. Signed callbacks with malformed status types are safely ignored, and non-ASCII incorrect verification tokens fail closed. Ambiguous duplicate provider IDs are ignored instead of updating multiple jobs. `GET /api/jobs` exposes the new fields alongside all existing ones. Unknown IDs and unrelated inbound messages are acknowledged and ignored.

For frontend monitoring, `GET /api/jobs` also returns `delivery_verified` and `delivery_source`. These are `true` and `webhook` only for a real-test job on the real-test channel with a provider ID, confirmed `delivered`/`read` delivery status, and a recorded delivery time. Accepted and simulated jobs return `false` and `null`. These are evidence fields derived from stored webhook results, not client-supplied claims. No additional migration is needed for the derived fields.

After webhook verification succeeds, **only if you intentionally approve one more test**, use the existing controlled automatic test procedure above with a fresh purchase key and the existing verified allowlisted recipient. Keep the webhook-only listener and tunnel running while that one job is sent, then return the normal API to simulation mode. Do not reuse or reset a previous job. Provider acceptance alone is not delivery; check that the job's `delivery_status` advances through signed Meta callbacks. Quick Tunnels have no uptime guarantee, and a callback arriving before the sender persists a new message ID may be ignored; reconcile such cases manually rather than resending automatically.
