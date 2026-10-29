# LocalGrowth AI API Contract

**Status:** Initial shared contract  
**Base path:** `/api`  
**Stack:** FastAPI + SQLite backend; HTML, CSS, and JavaScript frontend

This document defines the first shared frontend/backend contract. Examples are synthetic. All request and response bodies use JSON (`Content-Type: application/json`) unless a response has no body. Timestamps are UTC ISO 8601 strings ending in `Z`.

## Conventions

- IDs are opaque resource-prefixed strings whose value contains a UUID (for example, `cus_0192e40a-44a2-7abc-8d0a-123456789abc`). Clients must not parse them or assume sequential IDs.
- Phone numbers in write requests use E.164 format (for example, `+14155550123`). **Every phone number returned by the API is masked**; the full number is never included in response payloads. The default mask preserves the country code and last two digits: `+1********23`.
- A purchase request carries two independent permission values: `thank_you` and `marketing`. `thank_you: true` permits a purchase-related thank-you/service message. `marketing: true` records explicit marketing opt-in. Neither permission implies the other. Marketing campaigns may only target customers with `marketing: true`.
- Campaign preview and simulation are non-delivery operations. `simulate` records synthetic outcomes and must not send real SMS or other messages.
- Lists use `data` and `pagination`. `limit` defaults to 20 and is capped at 100; `cursor` is an opaque value returned by the preceding page. Omitting `cursor` requests the first page.
- Unknown fields may be ignored by clients. The backend may reject unknown enum values and malformed fields.

## Common error response

All error responses use this shape. `details` is optional and contains field-level validation information when available.

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [
      { "field": "purchase.total_amount", "issue": "Must be greater than zero." }
    ],
    "request_id": "req_0192e40a-44a2-7abc-8d0a-123456789abc"
  }
}
```

| HTTP status | Error code | Meaning |
| --- | --- | --- |
| 400 | `INVALID_REQUEST` | Malformed JSON or invalid request semantics. |
| 401 | `UNAUTHENTICATED` | Authentication is required or invalid. |
| 403 | `FORBIDDEN` | Caller cannot perform the operation. |
| 404 | `NOT_FOUND` | Customer, campaign, or other requested resource does not exist. |
| 409 | `CONFLICT` | Duplicate/idempotency conflict or operation is not valid for the resource's current status. |
| 422 | `VALIDATION_ERROR` | A request field failed schema validation. |
| 500 | `INTERNAL_ERROR` | Unexpected server error. |

Clients should display `message` where appropriate and use `code` for behavior; they should not depend on `details` wording. Errors must not expose unmasked phone numbers.

## Shared schemas and enums

### Permission values

```json
{
  "permissions": {
    "thank_you": true,
    "marketing": false
  }
}
```

Both booleans are required when creating a purchase/customer record. `thank_you` applies only to transactional thank-you/service messages. `marketing` is an explicit opt-in and is required for marketing audience eligibility. A false value is an explicit denial or absence of permission. Permission changes are outside this initial contract.

### Campaign status

| Status | Meaning |
| --- | --- |
| `draft` | Created by preview; not approved. |
| `approved` | Explicitly approved for simulation. |
| `simulating` | Simulation is being processed. |
| `simulated` | Simulation completed; no real messages were sent. |
| `failed` | Preview or simulation processing failed. |

Allowed lifecycle: `draft` → `approved` → `simulating` → `simulated`; a processing failure may transition to `failed`. Approval is valid only for a `draft`; simulation is valid only for an `approved` campaign.

### Message job status

| Status | Meaning |
| --- | --- |
| `queued` | Job is waiting for the simulation worker. |
| `simulated` | Synthetic delivery outcome was recorded; no message was sent. |
| `skipped` | Job was not simulated, for example because the customer lacks marketing permission. |
| `failed` | Simulation processing failed. |

Jobs have `job_type` `thank_you` or `marketing`. Marketing jobs must be skipped for customers without marketing permission. A thank-you job can only be created when `thank_you` permission is true.

## Endpoints

### `POST /api/purchases`

Record a purchase and its customer details/permissions. The backend creates a customer when needed, stores the purchase, and returns both IDs. The backend may match an existing customer by normalized phone number. The raw phone number is accepted on input and never echoed in the response.

**Request**

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `customer.name` | string | yes | Display name, 1–120 characters. |
| `customer.phone` | string | yes | E.164 phone number. |
| `permissions.thank_you` | boolean | yes | Transactional thank-you/service permission. |
| `permissions.marketing` | boolean | yes | Explicit marketing opt-in. |
| `purchase.total_amount` | number | yes | Positive decimal amount. |
| `purchase.currency` | string | yes | Three-letter ISO 4217 currency code. |
| `purchase.items` | array | yes | At least one item. |
| `purchase.items[].name` | string | yes | Item name. |
| `purchase.items[].quantity` | integer | yes | Positive whole number. |
| `purchase.items[].unit_price` | number | yes | Non-negative decimal amount. |
| `purchase.purchased_at` | string | no | UTC timestamp; defaults to server time. |
| `idempotency_key` | string | no | Client-generated key for safe retries. Reuse with a different body returns `409`. |

**Example request**

```json
{
  "idempotency_key": "pos-2026-10-29-000184",
  "customer": {
    "name": "Maya Patel",
    "phone": "+14155550123"
  },
  "permissions": {
    "thank_you": true,
    "marketing": false
  },
  "purchase": {
    "total_amount": 38.50,
    "currency": "USD",
    "items": [
      { "name": "Herbal tea", "quantity": 2, "unit_price": 12.00 },
      { "name": "Ceramic mug", "quantity": 1, "unit_price": 14.50 }
    ],
    "purchased_at": "2026-10-29T10:15:00Z"
  }
}
```

**`201 Created` response**

```json
{
  "data": {
    "customer_id": "cus_0192e40a-44a2-7abc-8d0a-123456789abc",
    "purchase_id": "pur_0192e40a-44a2-7abc-8d0a-123456789abd",
    "customer": {
      "name": "Maya Patel",
      "phone_masked": "+1********23",
      "permissions": { "thank_you": true, "marketing": false }
    },
    "purchase": {
      "total_amount": 38.5,
      "currency": "USD",
      "purchased_at": "2026-10-29T10:15:00Z"
    }
  }
}
```

Possible errors: `400`, `401`, `422`, `500`.

### `GET /api/dashboard`

Return aggregate counts and recent activity for the dashboard. Does not return unmasked contact information.

**Query parameters:** none.

**`200 OK` response**

```json
{
  "data": {
    "customers_total": 128,
    "purchases_total": 342,
    "revenue": { "amount": 8472.25, "currency": "USD" },
    "campaigns": { "draft": 2, "approved": 1, "simulated": 8, "failed": 0 },
    "message_jobs": { "queued": 0, "simulated": 41, "skipped": 6, "failed": 1 },
    "recent_purchases": [
      {
        "purchase_id": "pur_0192e40a-44a2-7abc-8d0a-123456789abd",
        "customer_id": "cus_0192e40a-44a2-7abc-8d0a-123456789abc",
        "customer_name": "Maya Patel",
        "phone_masked": "+1********23",
        "total_amount": 38.5,
        "currency": "USD",
        "purchased_at": "2026-10-29T10:15:00Z"
      }
    ],
    "generated_at": "2026-10-29T10:16:02Z"
  }
}
```

Possible errors: `401`, `500`.

### `GET /api/customers`

List customers. The phone value is always masked. Permission values are returned separately so the frontend can distinguish transactional from marketing eligibility.

**Query parameters:** `limit` (optional integer, default 20, maximum 100), `cursor` (optional opaque string), `search` (optional name or phone search; phone input may be full or partial).

**`200 OK` response**

```json
{
  "data": [
    {
      "customer_id": "cus_0192e40a-44a2-7abc-8d0a-123456789abc",
      "name": "Maya Patel",
      "phone_masked": "+1********23",
      "permissions": { "thank_you": true, "marketing": false },
      "purchases_count": 3,
      "lifetime_value": { "amount": 86.5, "currency": "USD" },
      "created_at": "2026-10-12T09:00:00Z"
    }
  ],
  "pagination": { "limit": 20, "next_cursor": null }
}
```

Possible errors: `401`, `422`, `500`.

### `GET /api/jobs`

List message jobs created for thank-you messages or campaign simulations.

**Query parameters:** `limit` (optional integer, default 20, maximum 100), `cursor` (optional opaque string), `status` (optional `queued`, `simulated`, `skipped`, or `failed`), `campaign_id` (optional UUID), `job_type` (optional `thank_you` or `marketing`).

**`200 OK` response**

```json
{
  "data": [
    {
      "job_id": "job_0192e40a-44a2-7abc-8d0a-123456789abe",
      "job_type": "marketing",
      "status": "skipped",
      "customer_id": "cus_0192e40a-44a2-7abc-8d0a-123456789abc",
      "phone_masked": "+1********23",
      "campaign_id": "cam_0192e40a-44a2-7abc-8d0a-123456789abf",
      "reason": "marketing_permission_missing",
      "created_at": "2026-10-29T10:20:00Z",
      "completed_at": "2026-10-29T10:20:01Z"
    }
  ],
  "pagination": { "limit": 20, "next_cursor": null }
}
```

Possible errors: `401`, `422`, `500`.

### `POST /api/campaigns/preview`

Create a campaign draft and return an audience/message preview. Only customers whose `marketing` permission is true are eligible. This endpoint does not approve a campaign or send messages.

**Request**

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | yes | Campaign name, 1–120 characters. |
| `audience` | object | yes | Audience filters; empty object means all marketing-opted-in customers. |
| `audience.customer_ids` | array of UUID | no | Restrict to these customers. Permission filter still applies. |
| `audience.purchased_after` | string | no | Include customers with purchases at/after timestamp. |
| `message` | string | yes | Message body; 1–1000 characters. |
| `channel` | string | yes | Initial contract supports `sms`. |

**Example request**

```json
{
  "name": "November tea offer",
  "audience": { "purchased_after": "2026-09-01T00:00:00Z" },
  "message": "Enjoy 10% off your next tea order this November.",
  "channel": "sms"
}
```

**`201 Created` response**

```json
{
  "data": {
    "campaign_id": "cam_0192e40a-44a2-7abc-8d0a-123456789abf",
    "status": "draft",
    "name": "November tea offer",
    "channel": "sms",
    "eligible_customers_count": 17,
    "excluded_without_marketing_permission_count": 5,
    "message_preview": "Enjoy 10% off your next tea order this November.",
    "sample_recipients": [
      {
        "customer_id": "cus_0192e40a-44a2-7abc-8d0a-123456789abc",
        "name": "Maya Patel",
        "phone_masked": "+1********23"
      }
    ],
    "created_at": "2026-10-29T10:18:00Z"
  }
}
```

Possible errors: `400`, `401`, `422`, `500`.

### `POST /api/campaigns/{id}/approve`

Approve a draft campaign so that it can be simulated. This records approval and does not send messages. `{id}` is the `campaign_id` from preview.

**Request:** empty body.

**`200 OK` response**

```json
{
  "data": {
    "campaign_id": "cam_0192e40a-44a2-7abc-8d0a-123456789abf",
    "status": "approved",
    "approved_at": "2026-10-29T10:19:00Z"
  }
}
```

Possible errors: `401`, `404`, `409` (campaign is not in `draft`), `500`.

### `POST /api/campaigns/{id}/simulate`

Simulate campaign processing for eligible marketing recipients. Simulation never performs real delivery. `{id}` is the `campaign_id`.

**Request**

```json
{ "outcome_mode": "deterministic" }
```

`outcome_mode` is optional and currently supports only `deterministic`; omitted is equivalent. The backend may assign synthetic `simulated`, `skipped`, or `failed` job outcomes. Customers without marketing permission must be `skipped` and must not be marked as sent.

**`202 Accepted` response**

```json
{
  "data": {
    "campaign_id": "cam_0192e40a-44a2-7abc-8d0a-123456789abf",
    "status": "simulating",
    "jobs_created": 17,
    "jobs": [
      {
        "job_id": "job_0192e40a-44a2-7abc-8d0a-123456789abe",
        "customer_id": "cus_0192e40a-44a2-7abc-8d0a-123456789abc",
        "phone_masked": "+1********23",
        "job_type": "marketing",
        "status": "queued"
      }
    ],
    "started_at": "2026-10-29T10:20:00Z"
  }
}
```

The caller can poll `GET /api/jobs` and `GET /api/dashboard` for job/campaign progress. When complete, jobs have terminal status `simulated`, `skipped`, or `failed` and the campaign status is `simulated` or `failed`.

Possible errors: `400`, `401`, `404`, `409` (campaign is not `approved` or is already processing), `422`, `500`.

## Implementation notes for both clients

- Treat `customer_id`, `purchase_id`, `campaign_id`, and `job_id` as opaque strings and retain them when navigating between views.
- Do not render a phone number from local request state as if it were API-confirmed; responses contain only `phone_masked`.
- Keep the two permission flags separate in storage and UI. Marketing preview must exclude customers where `permissions.marketing` is false, regardless of `permissions.thank_you`.
- All monetary values are decimal amounts paired with a currency. Clients should display currency and amount together.
- This is the initial contract. Changes to field names, enums, or lifecycle behavior should be coordinated between frontend and backend and reflected here.
