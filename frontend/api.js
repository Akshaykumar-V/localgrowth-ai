import {createMock} from './mock.js';
const mock=createMock();
let mode=localStorage.getItem('lg-api-mode')==='mock'?'mock':'live';
export const getMode=()=>mode;
export function setMode(value){mode=value==='live'?'live':'mock';localStorage.setItem('lg-api-mode',mode);}
export class ApiError extends Error{constructor(payload){super(payload?.error?.message||'Something went wrong. Please retry.');this.code=payload?.error?.code;this.details=payload?.error?.details;}}
async function request(method,path,body){
 const requestMode=mode;
 try{
  if(requestMode==='mock'){await new Promise(r=>setTimeout(r,300));return structuredClone(await mock(method,path,body));}
  const response=await fetch(path,{method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(15000)});
  const result=await response.json();if(!response.ok)throw new ApiError(result);return result;
 }catch(error){if(error instanceof ApiError)throw error;if(error.error)throw new ApiError(error);throw new ApiError({error:{message:'Unable to reach the API. Check your connection and retry.'}});}
}
const query=params=>new URLSearchParams(Object.entries(params||{}).filter(([,v])=>v!==undefined&&v!==null)).toString();
export const api={dashboard:()=>request('GET','/api/dashboard'),customers:p=>request('GET',`/api/customers?${query(p)}`),jobs:p=>request('GET',`/api/jobs?${query(p)}`),purchase:body=>request('POST','/api/purchases',body),preview:body=>request('POST','/api/campaigns/preview',body),approve:id=>request('POST',`/api/campaigns/${encodeURIComponent(id)}/approve`),simulate:id=>request('POST',`/api/campaigns/${encodeURIComponent(id)}/simulate`,{outcome_mode:'deterministic'})};
export async function allPages(loader,params={}){const pageMode=mode;let data=[],cursor;do{const page=await loader({...params,limit:100,cursor});if(pageMode!==mode)throw new Error('API mode changed.');data.push(...page.data);cursor=page.pagination.next_cursor;}while(cursor);return data;}
