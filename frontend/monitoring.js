import {maskedPhone} from './integration.js';

const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const labels = {
  queued: 'Scheduled', processing: 'Processing', simulated: 'Simulated only',
  accepted: 'Accepted by WhatsApp API', failed: 'Failed', skipped: 'Skipped',
};
const explanations = {
  queued: 'Waiting for backend processing. Delivery has not been confirmed.',
  processing: 'The backend is processing this job. Delivery has not been confirmed.',
  simulated: 'Simulation outcome only. This status is not evidence of real sending or delivery.',
  accepted: 'WhatsApp API accepted the request. This does not confirm delivery.',
  failed: 'The job failed. Delivery has not been confirmed.',
  skipped: 'The job was not processed. Delivery has not been confirmed.',
};

export function messageStatus(job) {
  if (job.status === 'delivered') {
    // Proposed optional public evidence fields. A status string alone is not proof.
    const verified = job.delivery_verified === true && job.delivery_source === 'webhook' && job.channel !== 'whatsapp_simulated';
    return {key: verified ? 'delivered' : 'unverified', label: verified ? 'Delivered (webhook-confirmed)' : 'Delivery unverified', explanation: verified ? 'Delivery was confirmed by the provider webhook.' : 'The API reports delivery without webhook verification. It is not counted as delivered.'};
  }
  return {key: labels[job.status] ? job.status : 'unknown', label: labels[job.status] || 'Status unavailable', explanation: explanations[job.status] || 'The backend has not provided a supported message status.'};
}

// Error reasons may originate at a provider. Never show arbitrary credentials or contacts.
export function safeReason(reason) {
  if (!reason) return '';
  const known = {
    thank_you_permission_missing: 'Customer has not granted thank-you permission.',
    promotional_permission_missing: 'Customer has not granted promotional permission.',
    marketing_permission_missing: 'Customer has not granted promotional permission.',
    frequency_suppressed: 'A recent campaign prevents another promotional message.',
  };
  if (known[reason]) return known[reason];
  return String(reason)
    .replace(/(?:bearer\s+)[^\s,;]+/gi, '[credential hidden]')
    .replace(/(?:access[_ -]?token|authorization|api[_ -]?key|secret)["']?\s*[=:]\s*["']?[^\s,;"']+/gi, '[credential hidden]')
    .replace(/\b[A-Za-z0-9_.+\/-]{32,}={0,2}\b/g, '[credential hidden]')
    .replace(/(?<!\d)(?:\+\d{1,3}[\s()-]*)?\d(?:[\d\s()-]{7,18})\d(?!\d)/g, '[phone hidden]')
    .slice(0, 400);
}

export function publicJob(job) {
  return {
    job_id: job.job_id, campaign_id: job.campaign_id, job_type: job.job_type,
    phone_masked: maskedPhone(job.phone_masked), status: job.status, channel: job.channel,
    scheduled_at: job.scheduled_at, created_at: job.created_at,
    reason: safeReason(job.reason), delivery_verified: job.delivery_verified === true,
    delivery_source: job.delivery_source === 'webhook' ? 'webhook' : null,
  };
}

export function jobCounts(jobs) {
  const counts = {total: jobs.length, queued: 0, processing: 0, simulated: 0, accepted: 0, delivered: 0, failed: 0, skipped: 0, unverified: 0, unknown: 0};
  for (const job of jobs) counts[messageStatus(job).key]++;
  return counts;
}

function time(value) {
  if (!value || !Number.isFinite(Date.parse(value))) return null;
  return new Date(value).toLocaleString('en-IN', {day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata'}) + ' IST';
}

export function jobCardHTML(job) {
  const status = messageStatus(job);
  const type = {thank_you: 'Purchase thank-you', promotional: 'New-arrival promotion', marketing: 'New-arrival promotion'}[job.job_type] || 'Message';
  const schedule = time(job.scheduled_at);
  const channel = job.channel === 'whatsapp_simulated' ? 'Simulated WhatsApp channel' : 'Backend-managed message';
  return `<div class="job"><span class="job-icon" aria-hidden="true">${status.key === 'queued' || status.key === 'processing' ? '◷' : '·'}</span><div class="job-content"><strong>${type} <span class="badge job-status ${status.key}">${escape(status.label)}</span></strong><p>${escape(maskedPhone(job.phone_masked))} · ${channel}</p><time>${schedule ? 'Scheduled: ' + escape(schedule) : 'Scheduled time not provided'}${!schedule && time(job.created_at) ? ' · Created: ' + escape(time(job.created_at)) : ''}</time><p class="job-explanation">${escape(status.explanation)}</p>${job.reason ? `<p class="job-reason">Reason: ${escape(safeReason(job.reason))}</p>` : (status.key === 'failed' || status.key === 'skipped' ? '<p class="job-reason">The backend did not provide a reason.</p>' : '')}</div></div>`;
}

export function jobListHTML(jobs) {
  return jobs.length ? jobs.map(jobCardHTML).join('') : '<div class="empty">No message jobs yet.<br>A purchase with thank-you permission or an approved campaign simulation will create activity here.</div>';
}

export function activityHTML(state) {
  const counts = state.jobs === null ? null : jobCounts(state.jobs);
  const metrics = [['Total scheduled jobs', 'total'], ['Scheduled', 'queued'], ['Processing', 'processing'], ['Simulated only', 'simulated'], ['Meta-accepted', 'accepted'], ['Delivered · verified', 'delivered'], ['Failed', 'failed'], ['Skipped', 'skipped']];
  return `<div class="automation-metrics">${metrics.map(([label, key]) => `<div class="metric"><div class="metric-top">${label}</div><div class="metric-value">${counts ? counts[key] : '—'}</div>${key === 'total' ? '<small>Across all message statuses</small>' : key === 'accepted' ? '<small>Acceptance is not delivery</small>' : key === 'delivered' ? '<small>Provider webhook confirmed</small>' : ''}</div>`).join('')}</div>
    ${state.error ? `<div class="error" role="alert">Could not refresh message activity. Check the backend connection or try Refresh. ${state.jobs === null ? 'Counts are unavailable.' : 'Showing the last successful results; statuses may be out of date.'} Automatic refresh will retry.</div>` : ''}
    <p class="activity-update" role="status">${state.loading ? '<span class="spinner"></span>Refreshing message activity…' : state.updatedAt ? 'Last checked: ' + escape(time(state.updatedAt)) : 'Waiting for message activity.'} Updates every 5 seconds while this page is visible.</p>
    ${counts?.unverified ? `<div class="note">${counts.unverified} delivery ${counts.unverified === 1 ? 'claim needs' : 'claims need'} webhook verification and ${counts.unverified === 1 ? 'is' : 'are'} excluded from the delivered count.</div>` : ''}
    ${state.jobs === null ? (state.loading ? '<div class="loading" role="status">Loading scheduled and promotional jobs…</div>' : '<div class="empty">Message activity is unavailable. Use Refresh to try again.</div>') : jobListHTML(state.jobs)}`;
}

export function campaignResultsHTML(campaign, state) {
  if (!Number.isInteger(campaign.jobs_created) || campaign.jobs_created < 0) return '';
  // Keep the API's initial job statuses until a newer jobs-list response replaces them.
  const jobs = new Map((campaign.jobs || []).map(job => [job.job_id, publicJob(job)]));
  for (const job of state.jobs || []) if (job.campaign_id === campaign.campaign_id) jobs.set(job.job_id, job);
  const list = [...jobs.values()], counts = jobCounts(list);
  return `<div class="campaign-results"><h3>${campaign.jobs_created} simulation jobs created</h3><p class="muted">${counts.queued} scheduled · ${counts.processing} processing · ${counts.simulated} simulated only · ${counts.accepted} accepted · ${counts.delivered} webhook-confirmed · ${counts.failed} failed · ${counts.skipped} skipped</p><div class="note">These are the job statuses returned by the backend. Simulated means no real delivery is confirmed; accepted is not delivered.${state.error ? ' Status refresh is offline; these results may be out of date.' : ''}</div>${list.length ? jobListHTML(list) : '<div class="empty">No simulation jobs were created.</div>'}</div>`;
}

// A single-flight monitor shared by automatic and manual refreshes. All dependencies
// are injectable so timing, offline recovery, and navigation can be tested without a browser.
export function createJobsMonitor({load, onChange, isActive, interval = 5000, setTimer = setTimeout, clearTimer = clearTimeout, now = () => new Date().toISOString()}) {
  let state = {jobs: null, loading: false, error: null, updatedAt: null};
  let stopped = true, timer, inFlight, controller, generation = 0, restart = false;
  const active = () => !stopped && isActive();
  const emit = () => { if (active()) onChange({...state}); };
  function schedule() {
    clearTimer(timer);
    if (active()) timer = setTimer(refresh, interval);
  }
  function refresh() {
    if (!active()) return Promise.resolve();
    if (inFlight) return inFlight;
    clearTimer(timer);
    const ticket = generation;
    controller = new AbortController();
    state = {...state, loading: true}; emit();
    inFlight = Promise.resolve().then(() => load(controller.signal)).then(jobs => {
      if (!active() || ticket !== generation) return;
      state = {jobs: jobs.map(publicJob), loading: false, error: null, updatedAt: now()};
    }).catch(() => {
      if (active() && ticket === generation) state = {...state, loading: false, error: true};
    }).finally(() => {
      inFlight = null;
      if (active()) {
        emit();
        if (restart) { restart = false; refresh(); } else schedule();
      }
    });
    return inFlight;
  }
  return {
    refresh,
    start() { if (!stopped) return; stopped = false; generation++; if (inFlight) restart = true; else refresh(); },
    stop() { stopped = true; generation++; restart = false; clearTimer(timer); controller?.abort(); state = {...state, loading: false}; },
  };
}
