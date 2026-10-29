import test from 'node:test';
import assert from 'node:assert/strict';
import {createMock} from '../mock.js';
const body=(thank_you=false,marketing=false)=>({idempotency_key:crypto.randomUUID(),customer:{name:'Test Customer',phone:'+919999888877'},permissions:{thank_you,marketing},purchase:{total_amount:125,currency:'INR',items:[{name:'Tea',quantity:1,unit_price:125}]}});
test('purchase returns contract shape, masks phone, separates permissions and retries safely',async()=>{
 const request=createMock(),b=body(false,true),result=await request('POST','/api/purchases',b);
 assert.deepEqual(Object.keys(result.data).sort(),['customer','customer_id','purchase','purchase_id']);
 assert.equal(result.data.customer.phone_masked,'+91********77');
 assert.deepEqual(result.data.customer.permissions,{thank_you:false,marketing:true});
 assert.equal((await request('POST','/api/purchases',b)).data.purchase_id,result.data.purchase_id);
 await assert.rejects(request('POST','/api/purchases',{...b,purchase:{...b.purchase,total_amount:130}}),e=>e.error.code==='CONFLICT');
 const jobs=await request('GET','/api/jobs?job_type=thank_you');assert(!jobs.data.some(j=>j.customer_id===result.data.customer_id));
 for(const endpoint of ['/api/dashboard','/api/customers','/api/jobs'])assert(!JSON.stringify(await request('GET',endpoint)).includes(b.customer.phone));
});
test('thank-you consent creates queued job; validation uses common error envelope',async()=>{
 const request=createMock(),result=await request('POST','/api/purchases',body(true,false));
 assert((await request('GET','/api/jobs?job_type=thank_you&status=queued')).data.some(j=>j.customer_id===result.data.customer_id));
 await assert.rejects(request('POST','/api/purchases',{...body(),purchase:{total_amount:0}}),e=>e.error.code==='VALIDATION_ERROR'&&!!e.error.request_id);
});
test('category audience IDs still enforce marketing permission; approve required before simulate',async()=>{
 const request=createMock();const customers=(await request('GET','/api/customers')).data;
 const draft=(await request('POST','/api/campaigns/preview',{name:'New tea',audience:{customer_ids:customers.map(c=>c.customer_id)},message:'New tea is here.',channel:'sms'})).data;
 assert.equal(draft.eligible_customers_count,customers.filter(c=>c.permissions.marketing).length);
 assert.equal(draft.excluded_without_marketing_permission_count,customers.filter(c=>!c.permissions.marketing).length);
 const path='/api/campaigns/'+draft.campaign_id;
 await assert.rejects(request('POST',path+'/simulate',{}),e=>e.error.code==='CONFLICT');
 assert.equal((await request('POST',path+'/approve')).data.status,'approved');
 await assert.rejects(request('POST',path+'/approve'),e=>e.error.code==='CONFLICT');
 const simulation=(await request('POST',path+'/simulate',{outcome_mode:'deterministic'})).data;
 assert.equal(simulation.status,'simulating');assert.equal(simulation.jobs_created,draft.eligible_customers_count);
 await new Promise(r=>setTimeout(r,800));
 const jobs=(await request('GET','/api/jobs?campaign_id='+draft.campaign_id)).data;assert(jobs.every(j=>j.status==='simulated'));assert.equal(jobs.length,draft.eligible_customers_count);
 assert.equal((await request('GET','/api/dashboard')).data.campaigns.simulated,1);
});
test('empty audience and cursor pagination preserve the contract',async()=>{
 const request=createMock();const first=await request('GET','/api/customers?limit=2');assert.equal(first.data.length,2);assert(first.pagination.next_cursor);
 const second=await request('GET','/api/customers?limit=2&cursor='+first.pagination.next_cursor);assert.notEqual(first.data[0].customer_id,second.data[0].customer_id);
 const empty=await request('POST','/api/campaigns/preview',{name:'No matches',audience:{customer_ids:[]},message:'New stock',channel:'sms'});assert.equal(empty.data.eligible_customers_count,0);assert.deepEqual(empty.data.sample_recipients,[]);
});
