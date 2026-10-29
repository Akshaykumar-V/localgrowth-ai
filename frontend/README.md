# LocalGrowth AI frontend

Vanilla HTML/CSS/JavaScript. Requires Node.js 22 or later; no installation or build step.

```powershell
cd frontend
npm start
```

Open http://localhost:5173. Synthetic mock mode is the default. Use the API mode selector to switch modes; the preference is stored locally. Mock records reset on reload; customer phone inputs are never persisted. Mock mode intentionally creates a new customer for each purchase instead of retaining full phone numbers for matching.

To connect a running backend using only the shared contract routes:

```powershell
cd frontend
$env:API_TARGET = "http://127.0.0.1:8000"
npm start
```

Select **Live** in the header. The development server proxies `/api` to the backend, avoiding CORS setup. It does not implement authentication. Deploy static assets behind the same origin as the API for production.

```powershell
cd frontend
npm run check
```

Checks cover JavaScript syntax, phone masking, independent permissions, API response shapes, purchase retries, campaign lifecycle, audience filtering, empty results and pagination.

## Contract limits

- Shop name is configured directly in `index.html` and `app.js`; no shop-settings endpoint exists.
- The purchase amount input can be left blank while filling the form, but the API requires a positive amount. Saving requests the actual amount rather than inventing a monetary value. Truly saving without an amount requires a contract update.
- Category and product history live only in session memory and contain no phone numbers. The contract does not provide category storage or detailed purchase-history reads. Existing customers show API purchase counts and lifetime value, with an explicit history-unavailable state.
- Add New Stock prepares a campaign; there is no stock-write endpoint. Category matching uses only session history. The optional all-opted-in audience uses an empty audience filter. All audience IDs still pass through server-side marketing permission filtering.
- There is no real SMS delivery control. Approve authorizes only simulation. Simulation progress is read from `/api/jobs`; terminal results distinguish simulated, skipped and failed.
- Thank-you jobs have creation timestamps but no scheduling timestamp in this contract. Queued jobs are shown as scheduled without inventing a send time.
- Fonts use Google Fonts with local sans-serif fallbacks. All decorative graphics are original CSS/text; no borrowed assets.

Manual review: navigate all four sections; record with each permission combination; inspect masked customer/history data; generate a category or all-opted-in campaign; approve and simulate; switch to live without a backend to inspect error/retry; review at mobile width.
