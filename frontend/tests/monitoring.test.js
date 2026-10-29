import test from 'node:test';
import assert from 'node:assert/strict';
import {messageStatus, jobCounts, jobCardHTML, activityHTML, campaignResultsHTML, publicJob, safeReason, createJobsMonitor} from '../monitoring.js';

const job = (status, extra = {}) => ({job_id: 'job_test', job_type: 'thank_you', channel: 'whatsapp_test', phone_masked: '******3210', scheduled_at: '2026-10-30T10:00:00Z', created_at: '2026-10-30T09:53:00Z', status, ...extra});
const labels = {queued: 'Scheduled', processing: 'Processing', simulated: 'Simulated only', accepted: 'Accepted by WhatsApp API', delivered: 'Delivered (webhook-confirmed)', failed: 'Failed', skipped: 'Skipped'};

for (const [status, label] of Object.entries(labels)) {
  test(`message status ${status} uses a clear label and actual schedule`, () => {
    const row = job(status, status === 'delivered' ? {delivery_verified: true, delivery_source: 'webhook'} : {});
    assert.equal(messageStatus(row).label, label);
    const html = jobCardHTML(row);
    assert(html.includes(label)); assert(html.includes('******3210')); assert.match(html, /Scheduled:.*30 Oct 2026.*IST/);
    if (status === 'accepted') assert.match(html, /does not confirm delivery/);
    if (status === 'simulated') assert.match(html, /not evidence of real sending/);
  });
}

test('only explicit provider webhook evidence counts as delivered', () => {
  const verified = job('delivered', {delivery_verified: true, delivery_source: 'webhook'});
  const jobs = [job('accepted'), job('delivered'), job('delivered', {delivery_verified: true}), job('delivered', {delivery_verified: 'true', delivery_source: 'webhook'}), job('delivered', {delivery_verified: true, delivery_source: 'webhook', channel: 'whatsapp_simulated'}), verified];
  const counts = jobCounts(jobs);
  assert.equal(counts.total, 6); assert.equal(counts.accepted, 1); assert.equal(counts.delivered, 1); assert.equal(counts.unverified, 4);
  assert.match(jobCardHTML(job('delivered')), /Delivery unverified/);
  assert.equal(messageStatus(job('simulated', {delivery_verified: true, delivery_source: 'webhook'})).key, 'simulated');
});

test('all returned message types and states contribute to actual counts, unknowns stay unknown', () => {
  const jobs = Object.keys(labels).map(status => job(status, {job_type: 'promotional', delivery_verified: true, delivery_source: 'webhook'}));
  const counts = jobCounts(jobs);
  assert.equal(counts.total, 7); for (const status of Object.keys(labels)) assert.equal(counts[status], 1);
  assert.match(jobCardHTML(jobs[0]), /New-arrival promotion/);
  assert.equal(messageStatus(job('future_state')).label, 'Status unavailable');
  assert.equal(jobCounts([job('future_state')]).unknown, 1);
});

test('missing optional fields, masked phones and failure reasons render safely', () => {
  assert.match(jobCardHTML({status: 'failed'}), /Scheduled time not provided/);
  assert.match(jobCardHTML({status: 'failed'}), /did not provide a reason/);
  assert.match(jobCardHTML(job('skipped', {reason: 'promotional_permission_missing'})), /has not granted promotional permission/);
  const reason = 'Provider rejected +91 98765 43210 access_token="EASecretLongCredentialValueDoNotShow123456789" <script>bad</script>';
  const html = jobCardHTML(job('failed', {phone_masked: '+919876543210', reason}));
  assert(!html.includes('98765')); assert(!html.includes('EASecret')); assert(!html.includes('<script>')); assert(html.includes('&lt;script&gt;'));
  assert(!jobCardHTML(job('queued', {phone_masked: '*919876543210'})).includes('9876543210'));
  assert.match(safeReason('Bearer abc123'), /credential hidden/);
  assert(!safeReason('{"access_token":"short-private-value"}').includes('short-private-value'));
  const projected = publicJob({...job('queued'), phone: '+919876543210', access_token: 'never_keep'});
  assert(!('phone' in projected)); assert(!('access_token' in projected));
});

test('activity distinguishes initial loading, empty data, offline and stale results', () => {
  const initial = {jobs: null, loading: true, error: null, updatedAt: null};
  assert.match(activityHTML(initial), /Loading scheduled and promotional/);
  assert.match(activityHTML(initial), /—/);
  const empty = activityHTML({...initial, jobs: [], loading: false, updatedAt: '2026-10-30T10:00:00Z'});
  assert.match(empty, /No message jobs yet/); assert(!empty.includes('Loading scheduled'));
  const offline = activityHTML({...initial, loading: false, error: true});
  assert.match(offline, /Counts are unavailable/); assert.match(offline, /Automatic refresh will retry/);
  assert(!offline.includes('No message jobs yet'));
  const stale = activityHTML({...initial, jobs: [job('accepted')], loading: false, error: true});
  assert.match(stale, /last successful results/); assert.match(stale, /Accepted by WhatsApp API/);
});

test('campaign results keep initial API jobs and replace them with refreshed actual statuses', () => {
  const campaign = {campaign_id: 'cam_test', jobs_created: 1, jobs: [job('queued')]};
  assert.match(campaignResultsHTML(campaign, {jobs: null}), /1 simulation jobs created/);
  assert.match(campaignResultsHTML(campaign, {jobs: null}), /Scheduled/);
  const html = campaignResultsHTML(campaign, {jobs: [job('accepted', {campaign_id: 'cam_test'})]});
  assert.match(html, /1 accepted/); assert.match(html, /0 webhook-confirmed/);
  assert.match(html, /Accepted by WhatsApp API/);
  assert.match(campaignResultsHTML({...campaign, jobs_created: 0, jobs: []}, {jobs: []}), /No simulation jobs were created/);
});

function harness(load) {
  let visible = true, nextTimer = 0;
  const timers = new Map(), snapshots = [];
  const monitor = createJobsMonitor({load, onChange: state => snapshots.push(state), isActive: () => visible, setTimer: (callback, ms) => {const id = ++nextTimer; timers.set(id, {callback, ms}); return id;}, clearTimer: id => timers.delete(id), now: () => '2026-10-30T10:00:00Z'});
  return {monitor, timers, snapshots, hide() {visible = false; monitor.stop();}, show() {visible = true; monitor.start();}, async tick() {const [id, timer] = timers.entries().next().value; assert.equal(timer.ms, 5000); timers.delete(id); await timer.callback();}};
}

test('poll every five seconds, no overlapping manual/automatic requests, status transitions replace counts', async () => {
  let finish, calls = 0;
  const h = harness(() => {calls++; return new Promise(resolve => finish = resolve);});
  h.monitor.start(); const pending = h.monitor.refresh(); await Promise.resolve();
  assert.equal(calls, 1); assert.equal(h.monitor.refresh(), pending); assert.equal(h.timers.size, 0);
  finish([job('queued')]); await pending;
  assert.equal(h.snapshots.at(-1).jobs[0].status, 'queued'); assert.equal(h.timers.size, 1);
  const tick = h.tick(); await Promise.resolve(); assert.equal(calls, 2); assert.equal(h.timers.size, 0);
  finish([job('processing')]); await tick;
  assert.equal(jobCounts(h.snapshots.at(-1).jobs).processing, 1);
  const again = h.tick(); await Promise.resolve(); finish([job('accepted')]); await again;
  assert.equal(jobCounts(h.snapshots.at(-1).jobs).accepted, 1); assert.equal(jobCounts(h.snapshots.at(-1).jobs).delivered, 0);
  const unverified = h.tick(); await Promise.resolve(); finish([job('delivered')]); await unverified;
  assert.equal(jobCounts(h.snapshots.at(-1).jobs).delivered, 0); assert.equal(jobCounts(h.snapshots.at(-1).jobs).unverified, 1);
  const confirmed = h.tick(); await Promise.resolve(); finish([job('delivered', {delivery_verified: true, delivery_source: 'webhook'})]); await confirmed;
  assert.equal(jobCounts(h.snapshots.at(-1).jobs).delivered, 1);
  const backendBug = h.tick(); await Promise.resolve(); finish([job('simulated')]); await backendBug;
  assert.equal(jobCounts(h.snapshots.at(-1).jobs).delivered, 0); assert.equal(jobCounts(h.snapshots.at(-1).jobs).simulated, 1);
  h.monitor.stop(); assert.equal(h.timers.size, 0);
});

test('offline refresh preserves last data, automatically retries, and recovers', async () => {
  let calls = 0;
  const h = harness(async () => {calls++; if (calls === 2) throw new Error('offline'); return [job(calls === 1 ? 'queued' : 'simulated')];});
  h.monitor.start(); await h.monitor.refresh();
  await h.tick(); assert.equal(h.snapshots.at(-1).error, true); assert.equal(h.snapshots.at(-1).jobs[0].status, 'queued');
  await h.tick(); assert.equal(h.snapshots.at(-1).error, null); assert.equal(h.snapshots.at(-1).jobs[0].status, 'simulated');
  h.monitor.stop();
});

test('initial offline state does not invent zero counts and retries successfully', async () => {
  let available = false;
  const h = harness(async () => {if (!available) throw new Error('offline'); return [];});
  h.monitor.start(); await h.monitor.refresh();
  assert.equal(h.snapshots.at(-1).jobs, null); assert.equal(h.snapshots.at(-1).error, true);
  available = true; await h.tick(); assert.deepEqual(h.snapshots.at(-1).jobs, []);
  h.monitor.stop();
});

test('navigation or hidden page aborts pending work, suppresses stale results, and resumes without overlap', async () => {
  let finish, signal, calls = 0;
  const h = harness(currentSignal => {calls++; signal = currentSignal; return new Promise(resolve => finish = resolve);});
  h.monitor.start(); const first = h.monitor.refresh(); await Promise.resolve(); h.hide();
  assert(signal.aborted); const snapshots = h.snapshots.length;
  h.show(); assert.equal(calls, 1); finish([job('accepted')]); await first; await Promise.resolve();
  assert.equal(calls, 2); assert(!h.snapshots.slice(snapshots).some(s => s.jobs?.some(j => j.status === 'accepted')));
  const second = h.monitor.refresh(); finish([job('simulated')]); await second;
  assert.equal(h.snapshots.at(-1).jobs[0].status, 'simulated');
  h.hide(); assert.equal(h.timers.size, 0); const afterStop = h.snapshots.length;
  await h.monitor.refresh(); assert.equal(h.snapshots.length, afterStop); assert.equal(calls, 2);
});
