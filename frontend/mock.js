// Synthetic API transport. Request bodies are never persisted; phones are masked on ingress.
const now=()=>new Date().toISOString();
const id=prefix=>`${prefix}_${crypto.randomUUID()}`;
const permissions=(marketing=true)=>({thank_you:true,marketing});
const names=['Ananya Sharma','Rahul Mehta','Priya Nair','Arjun Patel','Meera Iyer','Dev Shah'];
export function createMock(){
 const customers=names.map((name,i)=>({customer_id:id('cus'),name,phone_masked:`+91********${23+i*7}`,permissions:permissions(i%3!==1),purchases_count:3+i,lifetime_value:{amount:450+i*175,currency:'INR'},created_at:'2026-10-12T09:00:00Z'}));
 const purchases=customers.slice(0,5).map((c,i)=>({purchase_id:id('pur'),customer_id:c.customer_id,customer_name:c.name,phone_masked:c.phone_masked,total_amount:180+i*65,currency:'INR',purchased_at:new Date(Date.now()-i*1800000).toISOString()}));
 const jobs=customers.slice(0,3).map(c=>({job_id:id('job'),job_type:'thank_you',status:'queued',customer_id:c.customer_id,phone_masked:c.phone_masked,campaign_id:null,reason:null,created_at:now(),completed_at:null}));
 const campaigns=new Map(),retries=new Map();
 function fail(message,code='VALIDATION_ERROR'){throw {error:{code,message,request_id:id('req')}};}
 function page(data,q){const limit=Math.min(Number(q.get('limit'))||20,100),offset=Number(q.get('cursor'))||0;return {data:data.slice(offset,offset+limit),pagination:{limit,next_cursor:offset+limit<data.length?String(offset+limit):null}};}
 return async function request(method,path,body){
  const u=new URL(path,'http://mock'),q=u.searchParams;
  if(method==='GET'&&u.pathname==='/api/dashboard')return {data:{customers_total:customers.length,purchases_total:customers.reduce((n,c)=>n+c.purchases_count,0),revenue:{amount:customers.reduce((n,c)=>n+c.lifetime_value.amount,0),currency:'INR'},campaigns:{draft:[...campaigns.values()].filter(c=>c.status==='draft').length,approved:[...campaigns.values()].filter(c=>c.status==='approved').length,simulated:[...campaigns.values()].filter(c=>c.status==='simulated').length,failed:0},message_jobs:Object.fromEntries(['queued','simulated','skipped','failed'].map(s=>[s,jobs.filter(j=>j.status===s).length])),recent_purchases:purchases.slice(0,5),generated_at:now()}};
  if(method==='GET'&&u.pathname==='/api/customers')return page(customers.filter(c=>!q.get('search')||c.name.toLowerCase().includes(q.get('search').toLowerCase())||c.phone_masked.includes(q.get('search'))),q);
  if(method==='GET'&&u.pathname==='/api/jobs')return page(jobs.filter(j=>['job_type','status','campaign_id'].every(k=>!q.get(k)||j[k]===q.get(k))),q);
  if(method==='POST'&&u.pathname==='/api/purchases'){
   if(!body.customer?.name?.trim()||!/^\+[1-9]\d{7,14}$/.test(body.customer?.phone)||!(body.purchase?.total_amount>0)||!body.purchase.items?.length||['thank_you','marketing'].some(k=>typeof body.permissions?.[k]!=='boolean'))fail('Check the customer, phone, amount and permission fields.');
   const signature=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(body))))).map(b=>b.toString(16).padStart(2,'0')).join('');
   if(retries.has(body.idempotency_key)){const prior=retries.get(body.idempotency_key);if(prior.signature!==signature)fail('This retry key was already used with different purchase details.','CONFLICT');return prior.result;}
   const phone=body.customer.phone;const masked=(phone.startsWith('+91')?'+91':phone.slice(0,2))+'********'+phone.slice(-2);
   // No raw phone or phone-derived identifier is retained by the demo.
   const c={customer_id:id('cus'),name:body.customer.name,phone_masked:masked,permissions:{...body.permissions},purchases_count:1,lifetime_value:{amount:body.purchase.total_amount,currency:body.purchase.currency},created_at:now()};customers.unshift(c);
   const p={purchase_id:id('pur'),customer_id:c.customer_id,customer_name:c.name,phone_masked:masked,total_amount:body.purchase.total_amount,currency:body.purchase.currency,purchased_at:now()};purchases.unshift(p);
   if(c.permissions.thank_you)jobs.unshift({job_id:id('job'),job_type:'thank_you',status:'queued',customer_id:c.customer_id,phone_masked:masked,campaign_id:null,reason:null,created_at:now(),completed_at:null});
   const result={data:{customer_id:c.customer_id,purchase_id:p.purchase_id,customer:{name:c.name,phone_masked:masked,permissions:c.permissions},purchase:{total_amount:p.total_amount,currency:p.currency,purchased_at:p.purchased_at}}};if(body.idempotency_key)retries.set(body.idempotency_key,{result,signature:signature});return result;
  }
  if(method==='POST'&&u.pathname==='/api/campaigns/preview'){
   if(!body.name?.trim()||!body.message?.trim()||body.message.length>1000||body.channel!=='sms')fail('A campaign name and message are required.');
   const audience=customers.filter(c=>!body.audience.customer_ids||body.audience.customer_ids.includes(c.customer_id)),eligible=audience.filter(c=>c.permissions.marketing);
   const c={campaign_id:id('cam'),status:'draft',name:body.name,channel:'sms',eligible_customers_count:eligible.length,excluded_without_marketing_permission_count:audience.length-eligible.length,message_preview:body.message,sample_recipients:eligible.slice(0,3).map(({customer_id,name,phone_masked})=>({customer_id,name,phone_masked})),created_at:now()};campaigns.set(c.campaign_id,{...c,recipients:eligible});return {data:c};
  }
  const match=u.pathname.match(/^\/api\/campaigns\/([^/]+)\/(approve|simulate)$/);
  if(method==='POST'&&match){const c=campaigns.get(match[1]);if(!c)fail('Campaign not found.','NOT_FOUND');if(match[2]==='approve'){if(c.status!=='draft')fail('Only drafts can be approved.','CONFLICT');c.status='approved';return {data:{campaign_id:c.campaign_id,status:c.status,approved_at:now()}};}
   if(c.status!=='approved')fail('Approve the campaign before simulation.','CONFLICT');c.status='simulating';const created=c.recipients.map(r=>({job_id:id('job'),customer_id:r.customer_id,phone_masked:r.phone_masked,job_type:'marketing',status:'queued'}));const started_at=now();jobs.unshift(...created.map(j=>({...j,campaign_id:c.campaign_id,reason:null,created_at:started_at,completed_at:null})));setTimeout(()=>{jobs.filter(j=>j.campaign_id===c.campaign_id).forEach(j=>{j.status='simulated';j.completed_at=now();});c.status='simulated';},700);return {data:{campaign_id:c.campaign_id,status:'simulating',jobs_created:created.length,jobs:created,started_at}};
  }
  fail('This endpoint is not in the shared API contract.','NOT_FOUND');
 };
}
