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
- Purchase success displays the returned thank-you job ID, status and scheduled time. Full phone inputs are cleared after a successful save and are never persisted by the frontend.
- Customer purchase counts and lifetime value come from the backend. Detailed product/category history is limited to records returned from purchases during this browser session and is labelled accordingly. The customer-list endpoint has no detailed-history payload.
- Campaign previews send product name, category, available stock and optional INR price with `whatsapp_simulated`. Backend matching and recent-campaign suppression determine `matching_opted_in_customer_count`. No browser audience IDs or local-history matching are used.
- Approval is required before simulation. Job polling displays queued, simulated, skipped and failed outcomes. No real WhatsApp delivery is configured or claimed. Leaving Automation discards its local preview; generate a fresh preview when returning.
- Shop name remains configured in the existing HTML/JavaScript. No new screens, billing, import or delivery features are included.
