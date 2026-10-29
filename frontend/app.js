import {api,getMode,setMode,allPages} from './api.js';
import {money, allowed, maskedPhone, purchasePayload, campaignPayload, requestScope} from './integration.js';
import {categories, storeName} from './demo.js';
import {jobCardHTML, activityHTML, campaignResultsHTML, createJobsMonitor, safeReason} from './monitoring.js';
const $=s=>document.querySelector(s),main=$('#main');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=v=>new Date(v).toLocaleString('en-IN',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Kolkata'});
const initials=name=>name.split(' ').map(n=>n[0]).slice(0,2).join('');
const badge=(text,neutral=false)=>`<span class="badge ${neutral?'neutral':''}">${esc(text)}</span>`;
const localHistory=new Map();let campaign=null,version=0,toastTimer,disposeView=()=>{};
const heading=(k,title,description,action='')=>`<div class="page-heading"><div><p class="eyebrow">${k}</p><h1>${title}</h1><p class="subtitle">${description}</p></div>${action}</div>`;
const errorHTML=e=>`<div class="error" role="alert">${esc(safeReason(e.message))}</div>`;
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),4500);}
function current(){return ['home','quick-sale','customers','automation'].includes(location.hash.slice(1))?location.hash.slice(1):'home';}
const jobHTML=jobCardHTML;
function jobList(jobs){return jobs.length?jobs.map(jobHTML).join(''):'<div class="empty">No scheduled thank-you jobs.<br>A purchase with thank-you permission creates one.</div>';}
async function render(){
 const token=++version;disposeView();disposeView=()=>{};const section=current();if(section!=='automation')campaign=null;document.querySelectorAll('[data-nav]').forEach(a=>{a.classList.toggle('active',a.dataset.nav===section);if(a.dataset.nav===section)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
 $('#breadcrumb').innerHTML=`Workspace <b>/</b> ${{home:'Home','quick-sale':'Quick Sale',customers:'Customers',automation:'Automation'}[section]}`;
 $('#api-mode').value=getMode();$('#mode-label').textContent=getMode()==='mock'?'Demo workspace':'Live API';$('#footer-mode').textContent=getMode()==='mock'?'Synthetic demo data':'Connected API · delivery is status-dependent';
 main.innerHTML='<div class="loading" role="status"><span class="spinner"></span>Getting your workspace ready…</div>';
 try{if(section==='quick-sale'){saleView();return;}if(section==='home'){const [d,j]=await Promise.all([api.dashboard(),allPages(api.jobs,{job_type:'thank_you',status:'queued'})]);if(token!==version)return;homeView(d.data,j);}if(section==='customers'){const customers=await allPages(api.customers);if(token!==version)return;customerView(customers);}if(section==='automation'){automationView();}}
 catch(e){if(token!==version)return;main.innerHTML=heading('WORKSPACE','Let’s try that again','Your workspace could not be loaded.')+errorHTML(e)+'<button class="button" id="retry">Retry loading</button>';$('#retry').onclick=render;}
}
function homeView(d,jobs){
 main.innerHTML=heading('YOUR CLOTHING STORE, AT A GLANCE','Good things are growing.',`Here’s what’s happening at ${storeName}.`,`<div class="date">◷ ${new Date().toLocaleDateString('en-IN',{weekday:'short',day:'numeric',month:'long',year:'numeric',timeZone:'Asia/Kolkata'})}</div>`)+
 `<div class="hero"><div><p class="eyebrow">EVERY SALE IS A NEW CONNECTION</p><h2>You curate the collection.<br>We’ll help bring customers back.</h2><p>Record clothing purchases, remember favourite styles, and follow up with a little thoughtful automation.</p><a class="button" href="#quick-sale"><span>＋</span> Record Purchase <span>↗</span></a></div><div class="hero-art" aria-hidden="true"><div class="orbit"></div><div class="orbit two"></div><span class="hero-star">✧</span><span class="plant">✳</span><div class="art-label"><span>♡</span> A little care. A little growth.</div></div></div>`+
 `<div class="metrics">${[['Total purchases',d.purchases_total,'Every purchase, remembered','▧'],['Total customers',d.customers_total,'Your neighbourhood connections','♧'],['Scheduled thank-yous',jobs.length,'Scheduled for backend processing','◷']].map(([label,value,note,icon])=>`<div class="metric"><div class="metric-top">${label}<span class="metric-icon">${icon}</span></div><div class="metric-value">${value.toLocaleString('en-IN')}</div><small>${note}</small></div>`).join('')}</div>`+
 `<div class="columns"><div class="panel"><div class="panel-head"><div><h2>Recent clothing purchases</h2><p>The latest outfits picked up at your counter</p></div><a class="text-link" href="#customers">Customers ↗</a></div>${d.recent_purchases.length?`<div class="table-wrap"><table><thead><tr><th>Customer</th><th>Purchase</th><th>Recorded</th></tr></thead><tbody>${d.recent_purchases.map(p=>`<tr><td><div class="customer-cell"><span class="avatar">${esc(initials(p.customer_name))}</span><div>${esc(p.customer_name)}<small>${esc(maskedPhone(p.phone_masked))}</small></div></div></td><td>${money(p.total_amount,p.currency)}<small class="muted" style="display:block;margin-top:4px">${esc(p.category)}</small></td><td class="muted">${date(p.purchased_at)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">Your first purchase starts the story.<br>Use Record Purchase to get started.</div>'}</div><div class="panel"><div class="panel-head"><div><h2>A little follow-up</h2><p>Scheduled thank-you jobs</p></div><span class="badge neutral">${jobs.length} queued</span></div>${jobList(jobs.slice(0,3))}<div class="note">♧ Only customers with separate thank-you permission are added. Check Automation activity for current outcomes.</div><div class="panel-head"><a class="text-link" href="#automation">View automation →</a></div></div></div><div class="bottom-note"><span>♧</span> Built on permission. Designed for lasting relationships.</div>`;
}
function saleView() {
  main.innerHTML = heading('A MOMENT AT THE COUNTER', 'Record a purchase', 'A simple sale today. A familiar face tomorrow.') + `
    <div class="columns"><div class="panel form-panel">
      <h2>Purchase details</h2><p class="subtitle">Start with your customer and what they picked up.</p>
      <div id="sale-success" role="status"></div>
      <form id="sale-form"><div class="form-grid">
        <label class="field">Customer name<input name="name" required maxlength="120" autocomplete="name" placeholder="e.g. Ananya Sharma"></label>
        <label class="field">Mobile number<input name="phone" required type="tel" autocomplete="tel" pattern="(?:\\+91)?[6-9][0-9]{9}" placeholder="9876543210"><small>Indian 10-digit number, optionally prefixed with +91. Only masked numbers appear after saving.</small></label>
        <label class="field">Purchased product<input name="product" required maxlength="160" placeholder="e.g. Indigo cotton kurti"></label>
        <label class="field">Purchased category<select name="category" required><option value="">Choose a category</option>${categories.map(c => `<option>${c}</option>`).join('')}</select><small>Saved with the purchase for category-based matching.</small></label>
        <label class="field wide">Purchase amount · INR <span class="muted">Optional</span><input name="amount" type="number" min="0.01" max="9999999999.99" step="0.01" placeholder="₹ 0.00"><small>Leave blank to record the purchase without an amount.</small></label>
      </div><div class="permissions"><h3>Let your customer choose</h3><p>Both are optional, independent, and unchecked by default.</p>
        <label class="check"><input name="thank_you" type="checkbox"><span>Purchase thank-you message<small>Customer agrees to a service message about this purchase.</small></span></label>
        <label class="check"><input name="promotional" type="checkbox"><span>New stock & offers<small>Customer explicitly agrees to promotional messages. This is a separate choice.</small></span></label>
      </div><div id="sale-error"></div><div class="form-actions"><button class="button" type="submit">Save Purchase <span>→</span></button><small>Full mobile numbers never appear in your customer list.</small></div></form>
    </div><div><div class="panel form-panel"><p class="eyebrow">A SMALL HABIT. A BIG DIFFERENCE.</p><h2>More than a receipt.</h2><p class="subtitle">Remember the styles your customers love, then make your next conversation a little more personal.</p><div class="message">“Thank you for shopping with ${storeName}. We hope you love your new outfit.”</div><div class="note">${getMode() === 'mock' ? 'Offline demo: records and message outcomes are synthetic and reset on reload.' : 'Live API: purchases are saved to the connected backend. Live mode does not mean real WhatsApp sending is enabled.'} Check the returned job status for what actually happened. Accepted is not delivered.</div></div></div></div>`;
  const form = $('#sale-form'), screenVersion = version;
  const scope = requestScope(() => screenVersion === version && current() === 'quick-sale' && form.isConnected);
  let key = crypto.randomUUID(), busy = false;
  form.addEventListener('input', () => { key = crypto.randomUUID(); scope.invalidate(); });
  form.onsubmit = async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    const valid = scope.start(), button = form.querySelector('button');
    $('#sale-error').innerHTML = ''; $('#sale-success').innerHTML = '';
    let body;
    try {
      body = purchasePayload(new FormData(form), key);
      busy = true; button.textContent = 'Saving purchase…';
      form.querySelectorAll('input,select,button').forEach(el => el.disabled = true);
      const {data} = await api.purchase(body);
      if (!valid()) return;
      const historyKey = `${getMode()}:${data.customer_id}`;
      localHistory.set(historyKey, [...(localHistory.get(historyKey) || []), ...data.purchase.items.map(item => ({product: item.name, category: item.category, amount: data.purchase.total_amount, currency: data.purchase.currency, date: data.purchase.purchased_at}))]);
      form.reset(); key = crypto.randomUUID();
      $('#sale-success').innerHTML = `<div class="success">Purchase saved successfully for ${esc(data.customer.name)} · ${esc(maskedPhone(data.customer.phone_masked))}.<br>${money(data.purchase.total_amount, data.purchase.currency)} · Thank-you permission: ${allowed(data.customer.permissions, 'thank_you') ? 'Granted' : 'Not granted'} · Promotions: ${allowed(data.customer.permissions, 'promotional') ? 'Opted in' : 'Not granted'}</div>${data.thank_you_job ? `<div class="panel"><div class="panel-head"><h3>Thank-you job created · initial status</h3></div>${jobHTML(data.thank_you_job)}<div class="note">This is the status returned when the purchase was saved. <a class="text-link" href="#automation">View current activity →</a></div></div>` : '<div class="note">No thank-you job was created for this purchase.</div>'}`;
      toast('Purchase recorded. A new connection remembered.');
    } catch (error) {
      if (valid()) $('#sale-error').innerHTML = errorHTML(error);
    } finally {
      if (body) body.customer.phone = '';
      busy = false;
      form.querySelectorAll('input,select,button').forEach(el => el.disabled = false);
      button.innerHTML = 'Save Purchase <span>→</span>';
    }
  };
}

function customerView(customers) {
  main.innerHTML = heading('THE PEOPLE BEHIND THE PURCHASES', 'Your neighbourhood, remembered.', 'Customer details, permission choices, and the purchases you’ve recorded.', '<a class="button" href="#quick-sale">＋ Record Purchase</a>') + `<div class="panel"><div class="panel-head customer-tools"><div><h2>Customers <span class="badge neutral">${customers.length}</span></h2><p>Phone numbers are always masked.</p></div><input class="search" id="search" type="search" placeholder="Search customer name or ID" aria-label="Search customers"></div><div id="customer-rows"></div></div><div class="note section-spacer">Purchase counts and lifetime value come from the backend. Detailed history below shows only purchases recorded during this browser session; it is not a complete customer history.</div>`;
  function rows() {
    const query = $('#search').value.toLowerCase();
    const list = customers.filter(c => `${c.name} ${c.customer_id}`.toLowerCase().includes(query));
    $('#customer-rows').innerHTML = list.length ? `<div class="table-wrap"><table><thead><tr><th>Customer / ID</th><th>Mobile</th><th>Purchases</th><th>Promotions</th><th>History</th></tr></thead><tbody>${list.map(c => `<tr><td><div class="customer-cell"><span class="avatar">${esc(initials(c.name))}</span><div>${esc(c.name)}<small class="id">${esc(c.customer_id)}</small></div></div></td><td>${esc(maskedPhone(c.phone_masked))}</td><td>${c.purchases_count}<small class="muted"> · ${money(c.lifetime_value.amount, c.lifetime_value.currency)}</small></td><td>${badge(allowed(c.permissions, 'promotional') ? 'Opted in' : 'No permission', !allowed(c.permissions, 'promotional'))}</td><td><button class="text-link" data-history="${esc(c.customer_id)}">View history ↓</button></td></tr>`).join('')}</tbody></table></div><div id="history-detail" aria-live="polite"></div>` : `<div class="empty">${customers.length ? 'No customers match that search.' : 'No customers yet. Record your first purchase to start building connections.'}</div>`;
    document.querySelectorAll('[data-history]').forEach(button => button.onclick = () => {
      const c = customers.find(c => c.customer_id === button.dataset.history);
      const history = localHistory.get(`${getMode()}:${c.customer_id}`) || [];
      $('#history-detail').innerHTML = `<div class="history"><h3>${esc(c.name)} · Purchase history</h3><p>${c.purchases_count} recorded purchases · Thank-you permission: ${allowed(c.permissions, 'thank_you') ? 'Granted' : 'Not granted'} · Promotions: ${allowed(c.permissions, 'promotional') ? 'Opted in' : 'Not granted'}</p><p class="muted">Purchases recorded in this browser session only</p>${history.length ? `<ul>${history.map(h => `<li>${esc(h.product)} · ${esc(h.category)} · ${money(h.amount, h.currency)} · ${date(h.date)}</li>`).join('')}</ul>` : 'No detailed purchases have been recorded in this browser session.'}</div>`;
      $('#history-detail').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    });
  }
  rows(); $('#search').oninput = rows;
}

function automationView() {
  main.innerHTML = heading('THOUGHTFUL FOLLOW-UPS', 'A little care, on autopilot.', 'Prepare a new-stock update and review it before running a simulation.') + `
    <div class="columns"><div class="panel form-panel"><h2>Add New Stock</h2><p class="subtitle">Turn a new clothing arrival into a friendly customer update.</p>
    <form id="stock-form"><div class="form-grid">
      <label class="field wide">Product name<input name="product" required maxlength="160" placeholder="e.g. Handwoven cotton saree"></label>
      <label class="field wide">Stock category<select name="category" required><option value="">Choose a category</option>${categories.map(c => `<option>${c}</option>`).join('')}</select></label>
      <label class="field">Available stock<input name="stock" required type="number" min="0" step="1" placeholder="e.g. 12"></label>
      <label class="field">Price · INR <span class="muted">Optional</span><input name="price" type="number" min="0" max="9999999999.99" step="0.01" placeholder="₹ 0.00"></label>
    </div><div class="note section-spacer">The backend matches previous purchases in this category with promotional permission and applies recent-campaign suppression. Approval authorizes a simulation only.</div><div id="stock-error"></div><div class="form-actions"><button class="button" type="submit">Generate preview <span>→</span></button></div></form></div>
    <div class="panel"><div class="panel-head"><div><h2>Your campaign preview</h2><p>Review first. Approve next. Simulate last.</p></div>${badge('Simulation only', true)}</div><div id="campaign-preview" class="preview"></div></div></div>
    <div class="panel section-spacer"><div class="panel-head"><div><h2>Automation activity</h2><p>All purchase thank-yous and new-arrival promotions</p></div><button id="refresh-jobs" class="text-link">Refresh ↻</button></div><div class="note">Live API mode is a backend connection, not proof of real WhatsApp sending. Simulated only means no real delivery is confirmed. WhatsApp API acceptance is not delivery; delivery requires provider webhook verification.</div><div id="message-activity" aria-busy="true"></div></div>`;
  const screenVersion = version, form = $('#stock-form');
  const isCurrent = () => screenVersion === version && current() === 'automation' && form.isConnected;
  const scope = requestScope(isCurrent);
  let activityState = {jobs: null, loading: true, error: null, updatedAt: null};
  const monitor = createJobsMonitor({
    isActive: () => isCurrent() && !document.hidden,
    load: signal => allPages(params => api.jobs(params, {signal})),
    onChange: state => {
      if (!isCurrent()) return;
      activityState = state;
      $('#message-activity').innerHTML = activityHTML(state);
      $('#message-activity').setAttribute('aria-busy', String(state.loading));
      $('#refresh-jobs').disabled = state.loading;
      $('#refresh-jobs').textContent = state.loading ? 'Refreshing…' : 'Refresh ↻';
      showCampaign();
    },
  });
  let previewBusy = false, actionBusy = false;

  function showCampaign() {
    if (!isCurrent()) return;
    const target = $('#campaign-preview');
    if (!campaign) { target.innerHTML = '<div class="empty">✧<br>Your next customer connection starts here.<br>Add a product to preview its message and eligible audience.</div>'; return; }
    const c = campaign, count = c.matching_opted_in_customer_count;
    target.innerHTML = `<span class="preview-label">Eligible matching customers</span><span class="audience-count">${count}</span><p class="muted">Category: ${esc(c.product.category)} · ${c.product.available_stock} in stock · ${money(c.product.price, c.product.currency)}</p>${count ? '' : '<div class="note">No eligible customers currently match this category. Promotional permission and recent-campaign suppression are checked by the backend.</div>'}<div class="message">${esc(c.message_preview)}</div><p class="muted">${c.sample_recipients.map(r => `${esc(r.name)} (${esc(maskedPhone(r.phone_masked))})`).join(' · ') || 'No sample recipients'}</p><p>${badge(c.status, true)}</p><div class="button-row"><button id="approve" class="button secondary" ${actionBusy || c.status !== 'draft' || !count ? 'disabled' : ''}>Approve</button><button id="simulate" class="button" ${actionBusy || c.status !== 'approved' ? 'disabled' : ''}>Simulate</button></div><div id="campaign-status" class="inline-status" aria-live="polite">${esc(c.result || 'Approval authorizes simulation only. No real WhatsApp messages are sent.')}</div><div id="campaign-error"></div>${campaignResultsHTML(c, activityState)}`;
    $('#approve').onclick = () => campaignAction('approve');
    $('#simulate').onclick = () => campaignAction('simulate');
  }

  form.oninput = () => {
    scope.invalidate(); campaign = null; actionBusy = false; showCampaign();
  };
  form.onsubmit = async event => {
    event.preventDefault();
    if (previewBusy || !form.reportValidity()) return;
    const valid = scope.start(), button = form.querySelector('button');
    campaign = null; actionBusy = false; showCampaign();
    $('#stock-error').innerHTML = '';
    try {
      const body = campaignPayload(new FormData(form));
      previewBusy = true; button.textContent = 'Preparing preview…';
      form.querySelectorAll('input,select,button').forEach(el => el.disabled = true);
      const {data} = await api.preview(body);
      if (!valid()) return;
      campaign = data; showCampaign();
    } catch (error) { if (valid()) $('#stock-error').innerHTML = errorHTML(error); }
    finally {
      previewBusy = false; form.querySelectorAll('input,select,button').forEach(el => el.disabled = false);
      button.innerHTML = 'Generate preview <span>→</span>';
    }
  };

  async function campaignAction(action) {
    if (actionBusy || !campaign || (action === 'approve' ? campaign.status !== 'draft' : campaign.status !== 'approved')) return;
    const c = campaign, valid = scope.start();
    actionBusy = true; showCampaign();
    $(`#${action}`).textContent = action === 'approve' ? 'Approving…' : 'Starting…';
    try {
      const {data} = await api[action](c.campaign_id);
      if (!valid() || campaign !== c) return;
      Object.assign(c, data); actionBusy = false;
      if (action === 'simulate') {
        c.result = `${data.jobs_created} simulation jobs created. Follow the returned statuses below.`;
      }
      showCampaign();
      if (action === 'simulate') monitor.refresh();
      toast(action === 'approve' ? 'Campaign approved for simulation.' : 'Simulation started. No real messages will be sent.');
    } catch (error) {
      if (!valid() || campaign !== c) return;
      actionBusy = false; showCampaign(); $('#campaign-error').innerHTML = errorHTML(error);
    }
  }

  $('#refresh-jobs').onclick = () => monitor.refresh();
  const visibilityChanged = () => { if (document.hidden) monitor.stop(); else if (isCurrent()) monitor.start(); };
  document.addEventListener('visibilitychange', visibilityChanged);
  disposeView = () => { monitor.stop(); scope.invalidate(); document.removeEventListener('visibilitychange', visibilityChanged); };
  $('#message-activity').innerHTML = activityHTML(activityState);
  if (!document.hidden) monitor.start();

  showCampaign();
}

window.addEventListener('hashchange', render);
$('#api-mode').onchange = event => {
  setMode(event.target.value); campaign = null; localHistory.clear(); render();
};
render();
