# LocalGrowth AI API Contract

**Status:** Shared frontend/backend contract  
**Base path:** `/api`  
**Stack:** Python + FastAPI + SQLite backend; HTML, CSS, and JavaScript frontend

This document is the shared contract for the first implementation. Examples are synthetic. Request and response bodies use JSON (`Content-Type: application/json`). Timestamps are UTC ISO 8601 strings ending in `Z`.

## Conventions

- IDs are opaque resource-prefixed strings containing UUID values, such as `cus_0192e40a-44a2-7abc-8d0a-123456789abc`. Clients must not parse IDs or assume they are sequential.
- Phone inputs accept either an Indian 10-digit number (for example, `9876543210`) or `+91` followed by 10 digits. The backend normalizes and stores them in `+91` E.164 form. **Every phone number in every response is masked to expose only its last four digits**, for example `******3210`. Full numbers must never appear in response payloads, including errors.
- Currency examples use INR. Purchase currency defaults to `INR`; monetary values may be omitted for the initial Quick Sale workflow.
- `permissions.thank_you` and `permissions.promotional` are independent. Each defaults to `false`. A permission is usable only when the customer made a recorded choice and the recorded value is `true`. An omitted choice remains false and unrecorded; explicitly providing `false` records a decline. Neither permission implies the other.
- No actual WhatsApp, SMS, or other communication may be transmitted by any endpoint in this contract. Campaign preview and simulation only create synthetic records. The only supported channel label is `whatsapp_simulated`.
- Lists use `data` and `pagination`. `limit` defaults to 20 and is capped at 100; `cursor` is an opaque value returned by the previous page. Omitting `cursor` requests the first page.
- Unknown enum values and malformed fields may be rejected. Clients must not depend on error `details` wording.

## Common error response

All errors use this shape. `details` is optional and contains field-level validation information when available. Error messages and details must not include a full phone number.

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [
      { "field": "purchase.items[0].category", "issue": "This field is required." }
    ],
    "request_id": "req_0192e40a-44a2-7abc-8d0a-123456789aaa"
  }
}
```

| HTTP status | Error code | Meaning |
| --- | --- | --- |
| 400 | `INVALID_REQUEST` | Malformed JSON or invalid request semantics. |
| 401 | `UNAUTHENTICATED` | Authentication is required or invalid. |
| 403 | `FORBIDDEN` | Caller cannot perform the operation. |
| 404 | `NOT_FOUND` | Customer, campaign, or other requested resource does not exist. |
| 409 | `CONFLICT` | Idempotency conflict or operation is invalid for the resource's current status. |
| 422 | `VALIDATION_ERROR` | A request field failed schema validation. |
| 500 | `INTERNAL_ERROR` | Unexpected server error. |
| 503 | `SERVICE_UNAVAILABLE` | Service or database is temporarily unavailable. |

Clients should use the stable `code` for behavior and display `message` where appropriate.

## Shared schemas and statuses

### Permissions

Purchase requests may omit either permission. Omission defaults to `false` and records no customer choice. Supplying `true` or `false` records the customer's choice and timestamp. Responses expose the recorded value and whether a choice was recorded:

```json
{
  "permissions": {
    "thank_you": { "allowed": true, "choice_recorded": true, "recorded_at": "2026-10-29T10:15:00Z" },
    "promotional": { "allowed": false, "choice_recorded": true, "recorded_at": "2026-10-29T10:15:00Z" }
  }
}
```

If omitted, a permission response is `{ "allowed": false, "choice_recorded": false, "recorded_at": null }`. A thank-you job requires a recorded `thank_you.allowed: true` choice. A promotional campaign recipient requires a recorded `promotional.allowed: true` choice.

### Campaign status

| Status | Meaning |
| --- | --- |
| `draft` | Created by preview; not approved. |
| `approved` | Explicitly approved for simulation. |
| `simulating` | Synthetic simulation is being processed. |
| `simulated` | Simulation completed; nothing was transmitted. |
| `failed` | Preview or simulation processing failed. |

Lifecycle: `draft` → `approved` → `simulating` → `simulated`; processing may instead end in `failed`. Approval is valid only for a `draft`; simulation is valid only for an `approved` campaign.

### Message job status

| Status | Meaning |
| --- | --- |
| `queued` | Waiting for the simulation worker. |
| `simulated` | A synthetic outcome was recorded; no message was transmitted. |
| `skipped` | Not simulated, for example because permission was not recorded as granted. |
| `failed` | Simulation processing failed. |

`job_type` is `thank_you` or `promotional`. `channel` is always `whatsapp_simulated`. Thank-you jobs require recorded thank-you permission; promotional jobs require recorded promotional permission. Thank-you job responses always include `scheduled_at`, an ISO 8601 timestamp. No job represents an actual message transmission.

## Endpoints

### `GET /api/health`

Return a basic application health status. This endpoint does not include customer data.

**Query parameters:** none.

**`200 OK` response**

```json
{
  "data": {
    "status": "ok",
    "database": "ok",
    "checked_at": "2026-10-29T10:16:02Z"
  }
}
```

Possible errors: `500` if the service cannot respond. A database problem may be returned as `503` with the common error body and code `SERVICE_UNAVAILABLE`.

### `POST /api/purchases`

Record one Quick Sale purchase and its customer details. The initial workflow requires exactly one purchased line item. Its product category is required; item quantity defaults to 1. The backend creates or matches a customer by normalized phone number, stores the purchase, and returns both IDs. A thank-you job may be created only if thank-you permission was explicitly granted. The raw phone number is accepted on input and never echoed in the response.

**Request**

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `customer.name` | string | yes | Display name, 1–120 characters. |
| `customer.phone` | string | yes | Indian 10-digit number or `+91` E.164 number; normalized to `+91` E.164. |
| `permissions.thank_you` | boolean | no | Defaults to false. If supplied, records the customer's choice. |
| `permissions.promotional` | boolean | no | Defaults to false. If supplied, records the customer's choice. |
| `purchase.total_amount` | number | no | Positive decimal amount when supplied. Optional for Quick Sale. |
| `purchase.currency` | string | no | ISO 4217 code; defaults to `INR`. |
| `purchase.items` | array | yes | Exactly one line item initially. |
| `purchase.items[0].name` | string | yes | Product name. |
| `purchase.items[0].category` | string | yes | Product category, such as `Sarees`, `Shirts`, or `Kurtis`. |
| `purchase.items[0].quantity` | integer | no | Positive whole number; defaults to 1. |
| `purchase.items[0].unit_price` | number | no | Non-negative decimal amount when supplied. |
| `purchase.purchased_at` | string | no | UTC timestamp; defaults to server time. |
| `idempotency_key` | string | no | Client-generated retry key. Reuse with a different body returns `409`. |

**Example request**

```json
{
  "idempotency_key": "pos-2026-10-29-000184",
  "customer": {
    "name": "Maya Patel",
    "phone": "9876543210"
  },
  "permissions": {
    "thank_you": true,
    "promotional": false
  },
  "purchase": {
    "currency": "INR",
    "items": [
      { "name": "Indigo cotton saree", "category": "Sarees", "quantity": 1 }
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
      "phone_masked": "******3210",
      "permissions": {
        "thank_you": { "allowed": true, "choice_recorded": true, "recorded_at": "2026-10-29T10:15:00Z" },
        "promotional": { "allowed": false, "choice_recorded": true, "recorded_at": "2026-10-29T10:15:00Z" }
      }
    },
    "purchase": {
      "total_amount": null,
      "currency": "INR",
      "items": [
        { "name": "Indigo cotton saree", "category": "Sarees", "quantity": 1, "unit_price": null }
      ],
      "purchased_at": "2026-10-29T10:15:00Z"
    },
    "thank_you_job": {
      "job_id": "job_0192e40a-44a2-7abc-8d0a-123456789abe",
      "job_type": "thank_you",
      "channel": "whatsapp_simulated",
      "status": "queued",
      "scheduled_at": "2026-10-29T10:15:00Z"
    }
  }
}
```

`thank_you_job` is `null` when thank-you permission is not recorded as granted. Monetary values omitted in the request are returned as `null`, with currency defaulted to `INR`.

Possible errors: `400`, `401`, `422`, `500`.

### `GET /api/dashboard`

Return aggregate counts and recent activity. Phone numbers are masked.

**Query parameters:** none.

**`200 OK` response**

```json
{
  "data": {
    "customers_total": 128,
    "purchases_total": 342,
    "purchases_with_amount": 301,
    "revenue": { "amount": 847225.00, "currency": "INR" },
    "campaigns": { "draft": 2, "approved": 1, "simulating": 0, "simulated": 8, "failed": 0 },
    "message_jobs": { "queued": 0, "simulated": 41, "skipped": 6, "failed": 1 },
    "recent_purchases": [
      {
        "purchase_id": "pur_0192e40a-44a2-7abc-8d0a-123456789abd",
        "customer_id": "cus_0192e40a-44a2-7abc-8d0a-123456789abc",
        "customer_name": "Maya Patel",
        "phone_masked": "******3210",
        "category": "Sarees",
        "total_amount": null,
        "currency": "INR",
        "purchased_at": "2026-10-29T10:15:00Z"
      }
    ],
    "generated_at": "2026-10-29T10:16:02Z"
  }
}
```

`revenue` sums only purchases with a known amount; `purchases_with_amount` gives the count included in that sum. Possible errors: `401`, `500`.

### `GET /api/customers`

List customers with masked phone numbers and distinct permission records.

**Query parameters:** `limit` (optional integer, default 20, maximum 100), `cursor` (optional opaque string), `search` (optional name or phone search; a submitted phone search may be full or partial).

**`200 OK` response**

```json
{
  "data": [
    {
      "customer_id": "cus_0192e40a-44a2-7abc-8d0a-123456789abc",
      "name": "Maya Patel",
      "phone_masked": "******3210",
      "permissions": {
        "thank_you": { "allowed": true, "choice_recorded": true, "recorded_at": "2026-10-29T10:15:00Z" },
        "promotional": { "allowed": false, "choice_recorded": true, "recorded_at": "2026-10-29T10:15:00Z" }
      },
      "purchases_count": 3,
      "lifetime_value": { "amount": 8650.00, "currency": "INR" },
      "created_at": "2026-10-12T09:00:00Z"
    }
  ],
  "pagination": { "limit": 20, "next_cursor": null }
}
```

`lifetime_value` sums only purchases with a known amount. Possible errors: `401`, `422`, `500`.

### `GET /api/jobs`

List synthetic thank-you and promotional message jobs. Job records never indicate actual transmission.

**Query parameters:** `limit` (optional integer, default 20, maximum 100), `cursor` (optional opaque string), `status` (optional `queued`, `simulated`, `skipped`, or `failed`), `campaign_id` (optional campaign ID), `job_type` (optional `thank_you` or `promotional`).

**`200 OK` response**

```json
{
  "data": [
    {
      "job_id": "job_0192e40a-44a2-7abc-8d0a-123456789abe",
      "job_type": "thank_you",
      "channel": "whatsapp_simulated",
      "status": "queued",
      "customer_id": "cus_0192e40a-44a2-7abc-8d0a-123456789abc",
      "phone_masked": "******3210",
      "purchase_id": "pur_0192e40a-44a2-7abc-8d0a-123456789abd",
      "campaign_id": null,
      "scheduled_at": "2026-10-29T10:15:00Z",
      "reason": null,
      "created_at": "2026-10-29T10:15:00Z",
      "completed_at": null
    }
  ],
  "pagination": { "limit": 20, "next_cursor": null }
}
```

`scheduled_at` is required for every `thank_you` job. It may be `null` for a promotional job. Possible errors: `401`, `422`, `500`.

### `POST /api/campaigns/preview`

Create a campaign draft targeting customers associated with a product category. Preview only counts customers with a recorded promotional opt-in. It does not approve or transmit messages.

**Request**

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | yes | Campaign name, 1–120 characters. |
| `product.name` | string | yes | Product name used for campaign context. |
| `product.category` | string | yes | Category to match against purchase history, such as `Sarees`, `Shirts`, or `Kurtis`. |
| `product.available_stock` | integer | yes | Non-negative current stock count. |
| `product.price` | number | no | Optional non-negative product price in `INR`. |
| `message` | string | yes | Proposed message, 1–1000 characters. |
| `channel` | string | yes | Must be `whatsapp_simulated`. No actual messages may be transmitted. |

Matching customers are those with at least one purchase in the requested category and a recorded `promotional.allowed: true` choice. The count is evaluated when preview is created.

**Example request**

```json
{
  "name": "Kurtis in stock",
  "product": {
    "name": "Indigo cotton kurti",
    "category": "Kurtis",
    "available_stock": 12,
    "price": 1499.00
  },
  "message": "Our Indigo cotton kurti is available. View it in store.",
  "channel": "whatsapp_simulated"
}
```

**`201 Created` response**

```json
{
  "data": {
    "campaign_id": "cam_0192e40a-44a2-7abc-8d0a-123456789abf",
    "status": "draft",
    "name": "Kurtis in stock",
    "product": {
      "name": "Indigo cotton kurti",
      "category": "Kurtis",
      "available_stock": 12,
      "price": 1499.00,
      "currency": "INR"
    },
    "channel": "whatsapp_simulated",
    "matching_opted_in_customer_count": 17,
    "message_preview": "Our Indigo cotton kurti is available. View it in store.",
    "sample_recipients": [
      {
        "customer_id": "cus_0192e40a-44a2-7abc-8d0a-123456789abc",
        "name": "Maya Patel",
        "phone_masked": "******3210"
      }
    ],
    "created_at": "2026-10-29T10:18:00Z"
  }
}
```

If `product.price` is omitted, response `price` is `null`; product `currency` remains `INR`. Possible errors: `400`, `401`, `422`, `500`.

### `POST /api/campaigns/{id}/approve`

Approve a draft campaign for simulation. This records approval only; it does not send or schedule a real message. `{id}` is the `campaign_id` returned by preview.

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

Run synthetic WhatsApp campaign simulation for matching, promotional-opted-in recipients. This endpoint and the simulation worker must never transmit an actual WhatsApp, SMS, or other message. `{id}` is the `campaign_id`.

**Request**

```json
{ "outcome_mode": "deterministic" }
```

The body may be empty; `outcome_mode` is optional and currently supports only `deterministic`.

**`202 Accepted` response**

```json
{
  "data": {
    "campaign_id": "cam_0192e40a-44a2-7abc-8d0a-123456789abf",
    "status": "simulating",
    "channel": "whatsapp_simulated",
    "jobs_created": 17,
    "jobs": [
      {
        "job_id": "job_0192e40a-44a2-7abc-8d0a-123456789ac0",
        "customer_id": "cus_0192e40a-44a2-7abc-8d0a-123456789abc",
        "phone_masked": "******3210",
        "job_type": "promotional",
        "channel": "whatsapp_simulated",
        "status": "queued",
        "scheduled_at": null
      }
    ],
    "started_at": "2026-10-29T10:20:00Z"
  }
}
```

`jobs_created` equals the `matching_opted_in_customer_count` evaluated again at simulation time; consent is rechecked before each synthetic job is created. The caller can poll `GET /api/jobs` and `GET /api/dashboard` for progress. On completion, jobs have terminal status `simulated`, `skipped`, or `failed`; campaign status is `simulated` or `failed`. Nothing is transmitted.

Possible errors: `400`, `401`, `404`, `409` (campaign is not `approved` or is already processing), `422`, `500`.

## Implementation notes for both clients

- Treat `customer_id`, `purchase_id`, `campaign_id`, and `job_id` as opaque strings.
- Never display or log a full phone number from a response; the API only returns `phone_masked`, showing the final four digits.
- Keep `thank_you` and `promotional` choices separate, default each to false, and do not treat a missing choice as recorded consent. Only a recorded `true` choice permits the corresponding job.
- Quick Sale starts with exactly one line item; its category is required while price and total amount are optional. Currency defaults to INR.
- Campaign matching uses product category and customers with recorded promotional opt-in. Stock and optional price are returned as campaign context.
- The only supported communication channel is `whatsapp_simulated`; no endpoint may send a real message.
- Coordinate changes to field names, enums, or lifecycle behavior between frontend and backend and update this document with them.
