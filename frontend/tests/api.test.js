import test from 'node:test';
import assert from 'node:assert/strict';

const storage = new Map();
globalThis.localStorage = {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)};
const {api, getMode, setMode, allPages} = await import('../api.js');

test('central client defaults to live, retains purchase payload/key, and uses backend campaign routes', async () => {
  assert.equal(getMode(), 'live');
  const previous = globalThis.fetch, calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push({path, options});
    return new Response(JSON.stringify({data: {status: 'approved'}}), {headers: {'Content-Type': 'application/json'}});
  };
  try {
    const body = {idempotency_key: 'retry-same', customer: {name: 'Synthetic', phone: '9876543210'}, permissions: {thank_you: false, promotional: true}, purchase: {items: [{name: 'Cotton shirt', category: 'Shirts'}]}};
    await api.purchase(body); await api.purchase(body);
    assert.equal(calls[0].options.body, calls[1].options.body);
    assert.deepEqual(JSON.parse(calls[0].options.body), body);
    await api.approve('cam_test'); await api.simulate('cam_test');
    assert.equal(calls[2].path, '/api/campaigns/cam_test/approve'); assert.equal(calls[2].options.body, undefined);
    assert.deepEqual(JSON.parse(calls[3].options.body), {outcome_mode: 'deterministic'});
  } finally { globalThis.fetch = previous; }
});

test('central client preserves backend validation envelope and does not fall back to mock', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({error: {code: 'VALIDATION_ERROR', message: 'Request validation failed.', details: [{field: 'purchase.items.0.category', issue: 'Field required'}], request_id: 'req_test'}}), {status: 422});
  try {
    await assert.rejects(api.purchase({}), e => e.code === 'VALIDATION_ERROR' && e.details[0].field === 'purchase.items.0.category');
    globalThis.fetch = async () => { throw new TypeError('offline'); };
    await assert.rejects(api.dashboard(), /Unable to reach the API/);
    assert.equal(getMode(), 'live');
  } finally { globalThis.fetch = previous; }
});

test('pagination stops when API mode changes so results cannot mix sources', async () => {
  let calls = 0;
  try {
    await assert.rejects(allPages(async () => {
      calls++; setMode('mock'); return {data: [], pagination: {limit: 100, next_cursor: 'opaque'}};
    }), /API mode changed/);
    assert.equal(calls, 1);
  } finally { setMode('live'); }
});
