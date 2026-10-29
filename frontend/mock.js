// Offline transport mirroring backend/app/{schemas,main,services}.py.
// Only masked phones and one-way lookup hashes are retained in memory.
const now = () => new Date().toISOString();
const id = prefix => `${prefix}_${crypto.randomUUID()}`;
const channel = 'whatsapp_simulated';
const phoneInText = value => /(?<!\d)(?:\+91[\s()-]*)?[6-9](?:[\d\s()-]{8,14})\d(?!\d)/.test(value || '');
const names = ['Ananya Sharma', 'Rahul Mehta', 'Priya Nair', 'Arjun Patel', 'Meera Iyer', 'Dev Shah'];
const consent = (allowed = false, choice_recorded = false) => ({allowed, choice_recorded, recorded_at: choice_recorded ? now() : null});
const digest = async value => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)))).map(b => b.toString(16).padStart(2, '0')).join('');

export function createMock() {
  const customers = names.map((name, i) => ({customer_id: id('cus'), name, phone_masked: `******${3210 + i}`, permissions: {thank_you: consent(true, true), promotional: consent(i % 3 !== 1, true)}, purchases_count: 0, lifetime_value: {amount: 0, currency: 'INR'}, created_at: now()}));
  const purchases = customers.flatMap((c, i) => Array.from({length: 3 + i}, (_, n) => {
    c.purchases_count++; c.lifetime_value.amount += 180 + i * 65;
    return {purchase_id: id('pur'), customer_id: c.customer_id, customer_name: c.name, phone_masked: c.phone_masked, category: i % 2 ? 'Snacks' : 'Beverages', total_amount: 180 + i * 65, currency: 'INR', purchased_at: new Date(Date.now() - (n * 6 + i) * 1800000).toISOString()};
  })).sort((a, b) => b.purchased_at.localeCompare(a.purchased_at));
  const makeJob = (c, type, purchase_id = null, campaign_id = null) => ({job_id: id('job'), job_type: type, channel, status: 'queued', customer_id: c.customer_id, phone_masked: c.phone_masked, purchase_id, campaign_id, scheduled_at: type === 'thank_you' ? new Date(Date.now() + 420000).toISOString() : null, reason: null, created_at: now(), completed_at: null});
  const jobs = customers.slice(0, 3).map(c => makeJob(c, 'thank_you', purchases.find(p => p.customer_id === c.customer_id).purchase_id));
  const campaigns = new Map(), retries = new Map(), phoneLookups = new Map();
  function fail(message, code = 'VALIDATION_ERROR') { throw {error: {code, message, request_id: id('req')}}; }
  function page(data, query) {
    const limit = Math.min(Number(query.get('limit')) || 20, 100), offset = query.get('cursor') ? Number(atob(query.get('cursor'))) : 0;
    return {data: data.slice(offset, offset + limit), pagination: {limit, next_cursor: offset + limit < data.length ? btoa(String(offset + limit)) : null}};
  }
  function processJobs() {
    for (const j of jobs.filter(j => j.status === 'queued' && (j.scheduled_at ? Date.parse(j.scheduled_at) <= Date.now() : Date.parse(j.created_at) + 700 <= Date.now()))) {
      const c = customers.find(c => c.customer_id === j.customer_id), permission = c.permissions[j.job_type];
      j.status = permission.allowed && permission.choice_recorded ? 'simulated' : 'skipped';
      j.reason = j.status === 'skipped' ? `${j.job_type}_permission_missing` : null; j.completed_at = now();
    }
    for (const c of campaigns.values()) if (c.status === 'simulating' && jobs.filter(j => j.campaign_id === c.campaign_id).every(j => j.status !== 'queued')) c.status = 'simulated';
  }
  function eligible(category) {
    return customers.filter(c => c.permissions.promotional.allowed && c.permissions.promotional.choice_recorded && purchases.some(p => p.customer_id === c.customer_id && p.category.toLowerCase() === category.toLowerCase()) && !jobs.some(j => j.customer_id === c.customer_id && j.job_type === 'promotional' && ['queued', 'simulated'].includes(j.status) && Date.parse(j.created_at) >= Date.now() - 168 * 3600000));
  }
  async function dispatch(method, path, body) {
    processJobs();
    const url = new URL(path, 'http://mock'), query = url.searchParams;
    if (method === 'GET' && url.pathname === '/api/dashboard') return {data: {
      customers_total: customers.length, purchases_total: purchases.length, purchases_with_amount: purchases.filter(p => p.total_amount != null && p.currency === 'INR').length,
      revenue: {amount: customers.reduce((sum, c) => sum + c.lifetime_value.amount, 0), currency: 'INR'},
      campaigns: Object.fromEntries(['draft', 'approved', 'simulating', 'simulated', 'failed'].map(s => [s, [...campaigns.values()].filter(c => c.status === s).length])),
      message_jobs: Object.fromEntries(['queued', 'simulated', 'skipped', 'failed'].map(s => [s, jobs.filter(j => j.status === s).length])), recent_purchases: purchases.slice(0, 10), generated_at: now(),
    }};
    if (method === 'GET' && url.pathname === '/api/customers') return page(customers.filter(c => !query.get('search') || c.name.toLowerCase().includes(query.get('search').toLowerCase()) || c.phone_masked.includes(query.get('search'))), query);
    if (method === 'GET' && url.pathname === '/api/jobs') return page(jobs.filter(j => ['job_type', 'status', 'campaign_id'].every(k => !query.get(k) || j[k] === query.get(k))), query);
    if (method === 'POST' && url.pathname === '/api/purchases') {
      const item = body.purchase?.items?.[0], choices = body.permissions || {};
      const phone = String(body.customer?.phone || '').replace(/[\s()-]/g, '').replace(/^\+91(?=\d{10}$)/, '');
      if ([body.customer?.name, item?.name, item?.category].some(phoneInText)) fail('Phone numbers are not allowed in text fields.');
      if (!body.customer?.name?.trim() || !/^[6-9]\d{9}$/.test(phone) || !item?.name?.trim() || !item.category?.trim() || body.purchase.items.length !== 1 || (body.purchase.total_amount != null && !(body.purchase.total_amount > 0)) || (item.unit_price != null && item.unit_price < 0) || Object.keys(choices).some(k => !['thank_you', 'promotional'].includes(k) || typeof choices[k] !== 'boolean')) fail('Check the customer, phone, category, amount and permission fields.');
      const signature = await digest(JSON.stringify(body));
      if (body.idempotency_key && retries.has(body.idempotency_key)) {
        const prior = retries.get(body.idempotency_key); if (prior.signature !== signature) fail('Idempotency key was already used with a different request.', 'CONFLICT');
        return prior.result;
      }
      const lookup = await digest(phone);
      let c = customers.find(c => c.customer_id === phoneLookups.get(lookup));
      if (!c) {
        c = {customer_id: id('cus'), name: body.customer.name, phone_masked: `******${phone.slice(-4)}`, permissions: {thank_you: consent(), promotional: consent()}, purchases_count: 0, lifetime_value: {amount: 0, currency: 'INR'}, created_at: now()};
        customers.unshift(c); phoneLookups.set(lookup, c.customer_id);
      }
      c.name = body.customer.name;
      for (const kind of ['thank_you', 'promotional']) if (kind in choices) c.permissions[kind] = consent(choices[kind], true);
      const p = {purchase_id: id('pur'), customer_id: c.customer_id, customer_name: c.name, phone_masked: c.phone_masked, category: item.category, total_amount: body.purchase.total_amount ?? null, currency: body.purchase.currency || 'INR', purchased_at: body.purchase.purchased_at || now()};
      purchases.unshift(p); c.purchases_count++; if (p.currency === 'INR') c.lifetime_value.amount += p.total_amount ?? 0;
      const job = c.permissions.thank_you.allowed ? makeJob(c, 'thank_you', p.purchase_id) : null; if (job) jobs.unshift(job);
      const result = {data: {customer_id: c.customer_id, purchase_id: p.purchase_id, customer: {name: c.name, phone_masked: c.phone_masked, permissions: structuredClone(c.permissions)}, purchase: {total_amount: p.total_amount, currency: p.currency, items: [{name: item.name, category: item.category, quantity: item.quantity ?? 1, unit_price: item.unit_price ?? null}], purchased_at: p.purchased_at}, thank_you_job: job}};
      if (body.idempotency_key) retries.set(body.idempotency_key, {result, signature}); return result;
    }
    if (method === 'POST' && url.pathname === '/api/campaigns/preview') {
      const p = body.product;
      if ([body.name, body.message, p?.name, p?.category].some(phoneInText)) fail('Phone numbers are not allowed in text fields.');
      if (!body.name?.trim() || body.name.length > 120 || !body.message?.trim() || body.message.length > 1000 || body.channel !== channel || !p?.name?.trim() || !p.category?.trim() || !Number.isInteger(p.available_stock) || p.available_stock < 0 || (p.price != null && p.price < 0) || 'audience' in body) fail('Check the product, available stock and campaign details.');
      const recipients = eligible(p.category);
      const stock = p.available_stock === 0 ? `${p.name} (${p.category}) is currently out of stock.` : `${p.name} (${p.category}) is in stock. ${p.available_stock === 1 ? 'Only 1 in stock' : p.available_stock + ' in stock'}.`;
      const c = {campaign_id: id('cam'), status: 'draft', name: body.name, product: {...p, price: p.price ?? null, currency: 'INR'}, channel, matching_opted_in_customer_count: recipients.length, message_preview: `${stock}${p.price == null ? '' : ` Price: INR ${p.price.toFixed(2)}.`} ${body.message}`, sample_recipients: recipients.slice(0, 5).map(({customer_id, name, phone_masked}) => ({customer_id, name, phone_masked})), created_at: now()};
      campaigns.set(c.campaign_id, c); return {data: c};
    }
    const match = url.pathname.match(/^\/api\/campaigns\/([^/]+)\/(approve|simulate)$/);
    if (method === 'POST' && match) {
      const c = campaigns.get(match[1]); if (!c) fail('Campaign not found.', 'NOT_FOUND');
      if (match[2] === 'approve') {
        if (c.status !== 'draft') fail('Only a draft campaign can be approved.', 'CONFLICT');
        c.status = 'approved'; return {data: {campaign_id: c.campaign_id, status: c.status, approved_at: now()}};
      }
      if (c.status !== 'approved') fail('Only an approved campaign can be simulated.', 'CONFLICT');
      const created = eligible(c.product.category).map(r => makeJob(r, 'promotional', null, c.campaign_id)); jobs.unshift(...created);
      c.status = created.length ? 'simulating' : 'simulated';
      return {data: {campaign_id: c.campaign_id, status: c.status, channel, jobs_created: created.length, jobs: created.map(({job_id, customer_id, phone_masked, job_type, channel, status, scheduled_at}) => ({job_id, customer_id, phone_masked, job_type, channel, status, scheduled_at})), started_at: now()}};
    }
    fail('Resource not found.', 'NOT_FOUND');
  }
  return async (...args) => structuredClone(await dispatch(...args));
}
