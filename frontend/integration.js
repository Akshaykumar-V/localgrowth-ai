// Backend app/schemas.py and services.py define these payloads and response values.
import {storeName} from './demo.js';
export function money(amount, currency = 'INR') {
  if (amount === null || amount === undefined || amount === '') return 'Not recorded';
  return new Intl.NumberFormat('en-IN', {style: 'currency', currency, minimumFractionDigits: 2}).format(Number(amount));
}

export const allowed = (permissions, kind) => permissions?.[kind]?.allowed === true;
// Never fall back to a request's phone or an unmasked API value.
export const maskedPhone = value => typeof value === 'string' && /^(?:\+\d{1,3})?\*{4,}\d{2,4}$/.test(value) ? value : 'Number hidden';

function optionalAmount(form, field, minimum) {
  const value = String(form.get(field) ?? '').trim();
  if (!value) return undefined;
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount < minimum || amount > 9999999999.99 || !/^\d+(\.\d{1,2})?$/.test(value)) {
    throw new Error(`Enter a valid ${field === 'amount' ? 'purchase amount' : 'price'} with at most two decimal places.`);
  }
  return amount;
}

export function purchasePayload(form, idempotencyKey) {
  const total = optionalAmount(form, 'amount', 0.01);
  const name = String(form.get('name') ?? '').trim();
  const product = String(form.get('product') ?? '').trim();
  const category = String(form.get('category') ?? '').trim();
  if (!name || !product || !category) throw new Error('Enter the customer, product and category.');
  return {
    idempotency_key: idempotencyKey,
    customer: {name, phone: String(form.get('phone') ?? '').trim()},
    permissions: {thank_you: form.has('thank_you'), promotional: form.has('promotional')},
    purchase: {
      currency: 'INR',
      ...(total === undefined ? {} : {total_amount: total}),
      items: [{name: product, category, quantity: 1, ...(total === undefined ? {} : {unit_price: total})}],
    },
  };
}

export function campaignPayload(form) {
  const name = String(form.get('product') ?? '').trim();
  const category = String(form.get('category') ?? '').trim();
  const stock = String(form.get('stock') ?? '').trim();
  const price = optionalAmount(form, 'price', 0);
  if (!name || !category || !/^\d+$/.test(stock) || !Number.isSafeInteger(Number(stock))) {
    throw new Error('Enter a product, category and a whole available stock count.');
  }
  return {
    name: `New stock: ${name}`.slice(0, 120),
    product: {name, category, available_stock: Number(stock), ...(price === undefined ? {} : {price})},
    message: `Visit ${storeName} to find your next favourite outfit.`,
    channel: 'whatsapp_simulated',
  };
}

// Each screen/action owns a scope. Navigation, edits and newer requests invalidate older work.
export function requestScope(isCurrent) {
  let sequence = 0;
  return {
    invalidate() { sequence++; },
    start() { const ticket = ++sequence; return () => ticket === sequence && isCurrent(); },
  };
}
