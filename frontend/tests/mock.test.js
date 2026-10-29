import test from 'node:test';
import assert from 'node:assert/strict';
import {createMock} from '../mock.js';
import {allowed, money, purchasePayload, campaignPayload} from '../integration.js';

const sale = (thank_you = false, promotional = false) => ({idempotency_key: crypto.randomUUID(), customer: {name: 'Test Customer', phone: '9999888877'}, permissions: {thank_you, promotional}, purchase: {currency: 'INR', items: [{name: 'Cotton shirt', category: 'Kidswear', quantity: 1}]}});
const preview = () => ({name: 'New kidswear', product: {name: 'Cotton kidswear set', category: 'Kidswear', available_stock: 4, price: 125.5}, message: 'Visit us this week.', channel: 'whatsapp_simulated'});

test('optional amount purchase has backend response shape, separate consent objects and masked phone', async () => {
  const request = createMock(), body = sale(false, true), {data} = await request('POST', '/api/purchases', body);
  assert.deepEqual(Object.keys(data).sort(), ['customer', 'customer_id', 'purchase', 'purchase_id', 'thank_you_job']);
  assert.equal(data.customer.phone_masked, '******8877'); assert.equal(data.purchase.total_amount, null);
  assert.deepEqual(data.purchase.items, [{name: 'Cotton shirt', category: 'Kidswear', quantity: 1, unit_price: null}]);
  assert.equal(allowed(data.customer.permissions, 'thank_you'), false);
  assert.equal(allowed(data.customer.permissions, 'promotional'), true);
  assert.equal(data.customer.permissions.promotional.choice_recorded, true);
  assert(data.customer.permissions.promotional.recorded_at); assert.equal(data.thank_you_job, null);
  assert.equal(money(data.purchase.total_amount), 'Not recorded');
  for (const endpoint of ['/api/dashboard', '/api/customers', '/api/jobs']) assert(!JSON.stringify(await request('GET', endpoint)).includes(body.customer.phone));
});

test('thank-you consent returns the actual queued job and scheduled time', async () => {
  const request = createMock(), body = sale(true, false), {data} = await request('POST', '/api/purchases', body);
  const job = data.thank_you_job;
  assert.equal(job.job_type, 'thank_you'); assert.equal(job.channel, 'whatsapp_simulated');
  assert.equal(job.status, 'queued'); assert.equal(job.purchase_id, data.purchase_id);
  assert(Date.parse(job.scheduled_at) > Date.parse(job.created_at));
  assert.equal(allowed(data.customer.permissions, 'promotional'), false);
  const jobs = await request('GET', '/api/jobs?job_type=thank_you&status=queued');
  assert.deepEqual(jobs.data.find(j => j.job_id === job.job_id), job);
});

test('idempotency avoids duplicates, changed payload conflicts, repeat customer counts stay accurate', async () => {
  const request = createMock(), body = sale(true, true), first = (await request('POST', '/api/purchases', body)).data;
  assert.deepEqual((await request('POST', '/api/purchases', body)).data, first);
  await assert.rejects(request('POST', '/api/purchases', {...body, purchase: {...body.purchase, total_amount: 1}}), e => e.error.code === 'CONFLICT');
  const again = sale(false, false); again.customer.phone = '+919999888877';
  const second = (await request('POST', '/api/purchases', again)).data;
  assert.equal(first.customer_id, second.customer_id); assert.notEqual(first.purchase_id, second.purchase_id);
  const customer = (await request('GET', '/api/customers')).data.find(c => c.customer_id === first.customer_id);
  assert.equal(customer.purchases_count, 2); assert.equal(customer.lifetime_value.amount, 0);
  assert.equal(allowed(customer.permissions, 'promotional'), false);
});

test('category matching and suppression are transport-side; approval is required before simulation', async () => {
  const request = createMock();
  await request('POST', '/api/purchases', sale(false, true));
  const denied = sale(true, false); denied.customer.phone = '9999888878'; await request('POST', '/api/purchases', denied);
  const other = sale(false, true); other.customer.phone = '9999888879'; other.purchase.items[0].category = 'Other Clothing'; await request('POST', '/api/purchases', other);
  const draft = (await request('POST', '/api/campaigns/preview', preview())).data;
  assert.equal(draft.matching_opted_in_customer_count, 1);
  assert.equal(draft.product.currency, 'INR'); assert.equal(draft.channel, 'whatsapp_simulated');
  assert.match(draft.message_preview, /4 in stock/); assert.match(draft.message_preview, /INR 125.50/);
  assert.equal(draft.sample_recipients[0].phone_masked, '******8877');
  const path = '/api/campaigns/' + draft.campaign_id;
  await assert.rejects(request('POST', path + '/simulate', {}), e => e.error.code === 'CONFLICT');
  assert.equal((await request('POST', path + '/approve')).data.status, 'approved');
  await assert.rejects(request('POST', path + '/approve'), e => e.error.code === 'CONFLICT');
  const result = (await request('POST', path + '/simulate', {outcome_mode: 'deterministic'})).data;
  assert.equal(result.jobs_created, 1); assert.equal(result.status, 'simulating');
  assert(result.jobs.every(j => j.job_type === 'promotional' && j.channel === 'whatsapp_simulated' && j.status === 'queued'));
  assert.equal((await request('POST', '/api/campaigns/preview', preview())).data.matching_opted_in_customer_count, 0);
  await new Promise(resolve => setTimeout(resolve, 750));
  assert((await request('GET', '/api/jobs?campaign_id=' + draft.campaign_id)).data.every(j => j.status === 'simulated'));
  assert.equal((await request('GET', '/api/dashboard')).data.campaigns.simulated, 1);
});

test('empty campaigns complete without jobs; pagination, null values and validation match backend conventions', async () => {
  const request = createMock(), first = await request('GET', '/api/customers?limit=2');
  const second = await request('GET', '/api/customers?limit=2&cursor=' + encodeURIComponent(first.pagination.next_cursor));
  assert.equal(first.pagination.limit, 2); assert.notEqual(first.data[0].customer_id, second.data[0].customer_id);
  const p = preview(); p.product.category = 'Ethnic Wear'; delete p.product.price;
  const draft = (await request('POST', '/api/campaigns/preview', p)).data;
  assert.equal(draft.product.price, null); assert.equal(draft.matching_opted_in_customer_count, 0);
  await request('POST', `/api/campaigns/${draft.campaign_id}/approve`);
  assert.equal((await request('POST', `/api/campaigns/${draft.campaign_id}/simulate`, {})).data.status, 'simulated');
  const invalid = sale(); delete invalid.purchase.items[0].category;
  await assert.rejects(request('POST', '/api/purchases', invalid), e => e.error.code === 'VALIDATION_ERROR' && !!e.error.request_id);
});

test('real form payload builders work against the offline backend transport', async () => {
  const request = createMock(), form = new FormData();
  for (const [key, value] of Object.entries({name: 'Demo', phone: '9876543210', product: 'Cotton kidswear set', category: 'Kidswear', promotional: 'on'})) form.set(key, value);
  await request('POST', '/api/purchases', purchasePayload(form, 'demo-form-1'));
  const stock = new FormData();
  for (const [key, value] of Object.entries({product: 'Cotton kidswear set', category: 'Kidswear', stock: '12'})) stock.set(key, value);
  assert.equal((await request('POST', '/api/campaigns/preview', campaignPayload(stock))).data.matching_opted_in_customer_count, 1);
});
