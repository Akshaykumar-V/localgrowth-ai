import test from 'node:test';
import assert from 'node:assert/strict';
import {allowed, maskedPhone, money, purchasePayload, campaignPayload, requestScope} from '../integration.js';

const form = values => { const f = new FormData(); for (const [key, value] of Object.entries(values)) f.set(key, value); return f; };
const values = {name: 'Maya Demo', phone: '9876543210', product: 'Indigo kurti', category: 'Kurtis'};

test('purchase form sends category, independent flags and omits optional amount/unit_price', () => {
  const payload = purchasePayload(form({...values, thank_you: 'on'}), 'sale-1');
  assert.deepEqual(payload, {idempotency_key: 'sale-1', customer: {name: 'Maya Demo', phone: '9876543210'}, permissions: {thank_you: true, promotional: false}, purchase: {currency: 'INR', items: [{name: 'Indigo kurti', category: 'Kurtis', quantity: 1}]}});
  const withAmount = purchasePayload(form({...values, promotional: 'on', amount: '1499.50'}), 'sale-2');
  assert.equal(withAmount.purchase.total_amount, 1499.5); assert.equal(withAmount.purchase.items[0].unit_price, 1499.5);
  assert.deepEqual(withAmount.permissions, {thank_you: false, promotional: true});
  assert.throws(() => purchasePayload(form({...values, amount: '0'}), 'sale-3'));
});

test('stock form sends backend product and simulated channel, with no browser audience IDs', () => {
  const payload = campaignPayload(form({product: 'Blue kurti', category: 'Kurtis', stock: '4', price: '1599'}));
  assert.deepEqual(payload.product, {name: 'Blue kurti', category: 'Kurtis', available_stock: 4, price: 1599});
  assert.equal(payload.channel, 'whatsapp_simulated'); assert(!('audience' in payload));
  assert(!('price' in campaignPayload(form({product: 'Tea', category: 'Beverages', stock: '0'})).product));
  assert.throws(() => campaignPayload(form({product: 'Tea', category: 'Beverages', stock: '1.5'})));
});

test('backend permission objects with allowed false are not treated as truthy opt-ins', () => {
  const permissions = {thank_you: {allowed: true, choice_recorded: true, recorded_at: '2026-10-29T10:15:00Z'}, promotional: {allowed: false, choice_recorded: true, recorded_at: '2026-10-29T10:15:00Z'}};
  assert.equal(allowed(permissions, 'thank_you'), true); assert.equal(allowed(permissions, 'promotional'), false);
  assert.equal(allowed({}, 'promotional'), false);
  assert.equal(maskedPhone('******3210'), '******3210'); assert.equal(maskedPhone('+919876543210'), 'Number hidden');
  assert.equal(money(null), 'Not recorded'); assert.equal(money(undefined), 'Not recorded');
  assert.equal(money(0), '₹0.00'); assert.equal(money(1499.5), '₹1,499.50');
});

test('late results are ignored after navigation, edits, or a newer request', async () => {
  let current = true;
  const scope = requestScope(() => current);
  let resolve;
  const pending = new Promise(r => resolve = r), first = scope.start();
  const second = scope.start(); resolve('old response'); await pending;
  assert.equal(first(), false); assert.equal(second(), true);
  scope.invalidate(); assert.equal(second(), false);
  const third = scope.start(); current = false; assert.equal(third(), false);
});
