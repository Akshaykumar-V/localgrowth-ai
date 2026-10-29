# LocalGrowth AI frontend

Vanilla HTML/CSS/JavaScript, preserving the original Home, Quick Sale, Customers and Automation screens. Requires Node.js 22 or later; no build or npm install step.

The demo is for independent clothing retailers, using the fictional **Kaveri Fashion** store. Quick Sale and new-stock campaigns share these categories: Sarees, Shirts, T-Shirts, Kurtis, Dresses, Jeans, Ethnic Wear, Kidswear and Other Clothing. Offline sample purchases and campaign examples use clothing only.

## Run against FastAPI

Start the backend using [its setup instructions](../backend/README.md), then in a separate PowerShell terminal:

```powershell
cd frontend
$env:API_TARGET = "http://127.0.0.1:8000"
npm start
```

Open http://localhost:5173. **Live** is the default API mode. If this browser previously selected Mock, choose Live in the header. The existing `/api` development proxy forwards to `API_TARGET`, avoiding CORS setup. An unavailable backend produces a retryable error; the frontend never silently falls back to fake data.

For an offline demonstration, select **Mock**. Its synthetic responses follow the current backend implementation, including nested consent records, optional amounts, category matching, suppression and simulated WhatsApp jobs. Demo data resets on reload; only the mode preference persists.

## Checks

```powershell
cd frontend
npm run check
```

Checks include syntax, payload builders, independent permissions, nullable INR amounts, masked responses, scheduled jobs, idempotency, repeat-customer counts, backend-style category filtering, suppression, approval, simulation, pagination and stale request guards. The live test is skipped unless explicitly enabled against an isolated test backend.

To run the real backend smoke test without touching a shop database, use a Python environment with `backend/requirements.txt` installed. From the repository root:

```powershell
python frontend/tests/demo_backend.py
```

In another terminal:

```powershell
cd frontend
$env:API_TARGET = "http://127.0.0.1:8011"
$env:PORT = "5174"
npm start
```

Then run against the frontend proxy:

```powershell
cd frontend
$env:LOCALGROWTH_TEST_API = "http://127.0.0.1:5174"
npm run check
```

The live test writes only synthetic clothing purchases with a fresh test customer each run. `demo_backend.py` generates temporary secrets and a temporary SQLite database, and uses a two-second thank-you delay. The regular backend keeps its configured delay.

## Integration notes and limits

- Backend `app/schemas.py`, `app/main.py` and `app/services.py` are the integration source of truth. The older shared API contract is not used for payload mapping.
- Quick Sale sends one item with its category. Blank amount omits both `total_amount` and `unit_price`; responses with null amounts display “Not recorded”. The single-item amount is also its unit price when entered.
- Permission requests send independent `thank_you` and `promotional` booleans. Responses read each consent object's `.allowed`; unchecked choices explicitly record false.
- Purchase success displays whether the returned thank-you job was created, its actual initial status, and its scheduled time in IST. Full phone inputs are cleared after a successful save and are never persisted by the frontend.
- Customer purchase counts and lifetime value come from the backend. Detailed product/category history is limited to records returned from purchases during this browser session and is labelled accordingly. The customer-list endpoint has no detailed-history payload.
- Campaign previews send product name, category, available stock and optional INR price with `whatsapp_simulated`. Backend matching and recent-campaign suppression determine `matching_opted_in_customer_count`. No browser audience IDs or local-history matching are used.
- Approval is required before simulation. Results show the number of jobs created, their initial API statuses, and refreshed statuses from the jobs list. Leaving Automation discards its local preview; generate a fresh preview when returning.
- Shop name remains configured in the existing HTML/JavaScript. No new screens, billing, import or delivery features are included.

## Automation monitoring

Automation activity loads every page of `GET /api/jobs` without status or type filters, so thank-you and promotional jobs appear together. The counts come from those returned records. “Total scheduled jobs” counts all jobs across their current statuses; Scheduled counts only queued jobs. The existing dashboard aggregate is not used because the checked-out backend aggregate omits the newer states.

The page refreshes every five seconds after the preceding request completes. Automatic and manual refresh use one request at a time. Leaving the screen, changing API mode, or hiding the browser tab aborts polling; returning to the visible screen starts a fresh refresh. Failures retain and label the last successful results, and retries continue. Initial connection failures show unavailable counts instead of invented zeros. No new endpoint or browser delivery integration is introduced.

| API status | Shopkeeper label |
| --- | --- |
| `queued` | Scheduled |
| `processing` | Processing |
| `simulated` | Simulated only |
| `accepted` | Accepted by WhatsApp API |
| `delivered` with explicit webhook evidence | Delivered (webhook-confirmed) |
| `delivered` without that evidence | Delivery unverified |
| `failed` | Failed |
| `skipped` | Skipped |

Acceptance is never counted as delivery. A `simulated` job remains simulated even if a real test was intended; this frontend does not repair or conceal the backend sending bug. Live API mode means a backend connection, not that real WhatsApp sending is enabled. The campaign action remains **Simulate**; there is no real-send action.

### Backend compatibility and needed fields

The inspected local `backend/app/services.py` still exposes `whatsapp_simulated` and four message states. No real-send implementation or webhook-verification payload was present locally when this change was made. The frontend is compatible with the current payload and prepared for `processing`, `accepted` and `delivered` when the backend exposes them.

The existing public fields `job_id`, `job_type`, `campaign_id`, `phone_masked`, `status`, `scheduled_at`, `created_at` and `reason` drive monitoring. Missing schedules and reasons are explicitly labelled. Provider reasons are escaped and phone/credential-like content is redacted. Unknown fields are not copied into the monitoring snapshot.

For complete real-test integration, the backend must return the actual channel for each job and an authoritative delivery-verification signal. This frontend uses the following **optional compatibility fields**, which are not yet present in the inspected backend:

```json
{"status":"delivered","delivery_source":"webhook","delivery_verified":true}
```

Both evidence fields must be set by the backend only after validating a provider webhook. A job explicitly marked `whatsapp_simulated` is never counted as verified delivery. A plain `delivered` string, `completed_at`, or API acceptance is insufficient. Agree these field names with the backend implementation before claiming complete webhook integration; until then the UI displays delivery claims as unverified and excludes them from Delivered. A provider-confirmed delivery timestamp would also be useful for auditability. Do not expose credentials or backend environment settings in any public response.

Tests cover all message states, unverified delivery, status transitions including the simulated-only backend bug, loading/empty/offline rendering, masked contacts and redacted reasons, initial campaign results, five-second refresh, no overlap, cancellation, stale responses, recovery, and the existing clothing/consent/purchase/customer behavior.
