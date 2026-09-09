// Run against tsc output; no browser, model, token or database is involved.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const directory=process.argv[2];
const {prepare}=await import(pathToFileURL(directory+'/client.js'));
const {operations}=await import(pathToFileURL(directory+'/operations.js'));
const op=operations.knowledge_get_api_v1_pages_by_page_id;
assert.equal(prepare(op,{path:{page_id:'a/b?x=1%'}}).path,'/api/v1/pages/a%2Fb%3Fx%3D1%25');
for(const input of [{path:{page_id:'..'}},{path:{page_id:'ok'},query:{principal:'admin'}},{path:{page_id:'ok'},headers:{Authorization:'forged'}}]) assert.throws(()=>prepare(op,input));
const authorize=operations.auth_post_internal_v1_authorize;
const body={action:'read',space_id:'s'};
const request=prepare(authorize,{body});body.space_id='changed';
assert.equal(request.body,'{"action":"read","space_id":"s"}');
assert.throws(()=>prepare(authorize,{body:{value:NaN}}));
assert.equal('token' in request,false);
assert.equal(operations.retrieval_post_api_v1_search.response_schema,null);
assert.equal(operations.agent_get_api_v1_runs_by_run_id_events.transport,'sse');
console.log('TypeScript client boundary checks passed');
