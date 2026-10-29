import test from 'node:test';
import assert from 'node:assert/strict';
import {purchasePayload, campaignPayload, allowed, maskedPhone, money} from '../integration.js';

// Opt in only against the isolated backend launched by demo_backend.py.
const base = process.env.LOCALGROWTH_TEST_API;
test('real FastAPI integration through frontend proxy: purchase, consent, masking, matching, approval and simulation', {skip: !base}, async () => {
  const form = values => { const f = new FormData(); for (const [key, value] of Object.entries(values)) f.set(key, value); return f; };
  async function request(method, path, body, expected = 200) {
    const response = await fetch(new URL(path, base), {method, headers: {'Content-Type': 'application/json'}, ...(body ? {body: JSON.stringify(body)} : {})});
    const payload = await response.json(); assert.equal(response.status, expected, JSON.stringify(payload)); return payload.data;
  }
  const category = 'Shirts';
  const phone = '9' + String(crypto.getRandomValues(new Uint32Array(1))[0] % 1000000000).padStart(9, '0');
  const sale = purchasePayload(form({name: 'Synthetic Integration', phone, product: 'Cotton shirt', category, thank_you: 'on', promotional: 'on'}), crypto.randomUUID());
  const purchase = await request('POST', '/api/purchases', sale, 201);
  assert.equal(purchase.purchase.total_amount, null); assert.equal(purchase.purchase.items[0].unit_price, null);
  assert.equal(purchase.purchase.items[0].category, category);
  assert.equal(maskedPhone(purchase.customer.phone_masked), '******' + phone.slice(-4));
  assert.equal(allowed(purchase.customer.permissions, 'promotional'), true);
  assert.equal(allowed(purchase.customer.permissions, 'thank_you'), true);
  assert.equal(purchase.thank_you_job.channel, 'whatsapp_simulated'); assert(purchase.thank_you_job.scheduled_at);
  assert.equal((await request('POST', '/api/purchases', sale, 201)).purchase_id, purchase.purchase_id);
  await request('POST', '/api/purchases', {...sale, purchase: {...sale.purchase, total_amount: 1}}, 409);
  const repeat = purchasePayload(form({name: 'Synthetic Integration', phone: '+91' + phone, product: 'Cotton shirt', category, amount: '1499.50', thank_you: 'on', promotional: 'on'}), crypto.randomUUID());
  const second = await request('POST', '/api/purchases', repeat, 201);
  assert.equal(second.customer_id, purchase.customer_id); assert.equal(money(second.purchase.total_amount), '₹1,499.50');
  const customers = await request('GET', '/api/customers?limit=100');
  assert.equal(customers.find(c => c.customer_id === purchase.customer_id).purchases_count, 2);
  assert(!JSON.stringify(customers).includes(phone));
  const payload = campaignPayload(form({product: 'New cotton shirt', category, stock: '3', price: '125.50'}));
  const preview = await request('POST', '/api/campaigns/preview', payload, 201);
  assert.equal(preview.matching_opted_in_customer_count, 1); assert.match(preview.message_preview, /INR 125.50/);
  const path = `/api/campaigns/${preview.campaign_id}`;
  await request('POST', path + '/simulate', {}, 409);
  assert.equal((await request('POST', path + '/approve')).status, 'approved');
  const simulation = await request('POST', path + '/simulate', {outcome_mode: 'deterministic'}, 202);
  assert.equal(simulation.jobs_created, 1); assert.equal(simulation.channel, 'whatsapp_simulated');
  let jobs = [];
  for (let attempt = 0; attempt < 30; attempt++) {
    jobs = await request('GET', `/api/jobs?campaign_id=${preview.campaign_id}`);
    if (jobs.length && jobs.every(j => j.status !== 'queued')) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.equal(jobs[0].status, 'simulated'); assert.equal(jobs[0].job_type, 'promotional');
  assert(!JSON.stringify(jobs).includes(phone));
  assert.equal((await request('POST', '/api/campaigns/preview', payload, 201)).matching_opted_in_customer_count, 0);
  const deny = purchasePayload(form({name: 'Synthetic Integration', phone, product: 'Cotton shirt', category}), crypto.randomUUID());
  const optedOut = await request('POST', '/api/purchases', deny, 201);
  assert.equal(optedOut.thank_you_job, null); assert.equal(allowed(optedOut.customer.permissions, 'promotional'), false);
});
