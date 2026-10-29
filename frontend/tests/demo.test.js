import test from 'node:test';
import assert from 'node:assert/strict';
import {categories, storeName} from '../demo.js';
import {createMock} from '../mock.js';
import {campaignPayload} from '../integration.js';

test('Kaveri Fashion demo offers exactly the independent clothing retailer categories', () => {
  assert.equal(storeName, 'Kaveri Fashion');
  assert.deepEqual(categories, ['Sarees', 'Shirts', 'T-Shirts', 'Kurtis', 'Dresses', 'Jeans', 'Ethnic Wear', 'Kidswear', 'Other Clothing']);
});

test('offline sample purchases and generated campaigns use clothing with backend-compatible matching', async () => {
  const request = createMock();
  const {data: dashboard} = await request('GET', '/api/dashboard');
  assert(dashboard.recent_purchases.length > 0);
  assert(dashboard.recent_purchases.every(p => categories.includes(p.category) && p.currency === 'INR'));
  const form = new FormData();
  for (const [key, value] of Object.entries({product: 'Handwoven cotton saree', category: 'Sarees', stock: '8', price: '2499'})) form.set(key, value);
  const payload = campaignPayload(form);
  assert.match(payload.message, /Kaveri Fashion/);
  assert.match(payload.message, /outfit/);
  const {data: preview} = await request('POST', '/api/campaigns/preview', payload);
  assert.equal(preview.product.category, 'Sarees');
  assert.match(preview.message_preview, /Handwoven cotton saree/);
  assert.equal(preview.matching_opted_in_customer_count, 1);
  assert.equal(preview.channel, 'whatsapp_simulated');
});
