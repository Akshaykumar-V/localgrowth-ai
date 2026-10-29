import http from 'node:http';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
const types={'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml'};
http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname.startsWith('/api/')){
    const target=process.env.API_TARGET;
    if(!target){res.writeHead(503,{'Content-Type':'application/json'});return res.end(JSON.stringify({error:{code:'UNAVAILABLE',message:'Set API_TARGET before starting live mode.',request_id:'req_local'}}));}
    try{const chunks=[];for await(const c of req)chunks.push(c);const response=await fetch(new URL(url.pathname+url.search,target),{method:req.method,headers:{'Content-Type':'application/json'},body:['GET','HEAD'].includes(req.method)?undefined:Buffer.concat(chunks),signal:AbortSignal.timeout(15000)});res.writeHead(response.status,{'Content-Type':'application/json'});res.end(await response.text());}catch{res.writeHead(502,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{code:'UNAVAILABLE',message:'Could not reach the API. Please retry.',request_id:'req_local'}}));}return;
  }
  const file=path.resolve(root,'.'+decodeURIComponent(url.pathname==='/'?'/index.html':url.pathname));
  if(!file.startsWith(root+path.sep)){res.writeHead(403);return res.end();}
  try{const body=await readFile(file);res.writeHead(200,{'Content-Type':types[path.extname(file)]||'text/plain','Cache-Control':'no-store'});res.end(body);}catch{res.writeHead(404);res.end('Not found');}
}).listen(Number(process.env.PORT)||5173,'127.0.0.1',()=>console.log('LocalGrowth AI: http://localhost:5173'));
