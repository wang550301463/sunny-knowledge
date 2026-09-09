"""Only internal knowledge tools; every result retains complete original authorization support."""
from copy import deepcopy
from urllib.parse import quote

from pydantic import ValidationError

from .authorization import Guard
from .schemas import EvidenceRef, FeedbackCreate, fail
from .service import digest, encoded, merge_dependencies


class Tools:
    def __init__(self, service):
        self.service, self.clients = service, service.clients

    async def guard(self, token, run):
        guard = await Guard.begin(self.service.auth,token,owner=run['owner'])
        for space in run['actual_scope']:
            await guard.require('read',space)
        await guard.dependencies(self.clients,run['dependencies'])
        return guard

    @staticmethod
    def scope(run, name, requested):
        config = run['configuration']
        if name not in config['tools']:
            raise fail('tool_not_allowed','Tool is not enabled by the frozen configuration',403)
        permitted = set(run['actual_scope']) & set(config['space_ids']) & set(config['tool_space_ids'].get(name,config['space_ids']))
        if not requested or not set(requested) <= permitted:
            raise fail('tool_scope_denied','Tool request exceeds the effective knowledge scope',403)
        return sorted(requested)

    async def execute(self,token,run,call,args):
        name = call.function.name
        if name not in run['configuration']['tools']:
            raise fail('tool_not_allowed','Tool is disabled',403)
        guard = await self.guard(token,run)
        requested = getattr(args,'space_ids',None) or ([args.space_id] if hasattr(args,'space_id') else run['actual_scope'])
        spaces = self.scope(run,name,requested)
        if name in {'search','traverse','timeline'}:
            body = args.model_dump(mode='json')
            body.update({'space_ids':spaces,'as_of':run['request'].get('as_of'),'known_at':run['request'].get('known_at')})
            if name == 'traverse':
                if not set(args.seed_fragment_ids) <= set(run['checkpoint'].get('fragment_ids',[])):
                    raise fail('unknown_graph_seed','Graph seeds must come from this run',422)
            if name == 'timeline':
                for page_id in args.page_ids:
                    found = False
                    for space in spaces:
                        if await guard.allowed('read',space,page_id):
                            found = True
                    if not found:
                        raise fail('tool_scope_denied','Timeline resource is outside the authorized scope',403)
            await guard.finish()
            data = await self.clients.call('retrieval','POST','/internal/v1/'+name,token,body)
            if name == 'timeline':
                return await self.timeline_result(guard,run,data,spaces)
            return await self.search_result(guard,run,data,spaces)
        if name == 'get':
            await guard.require('read',args.space_id,args.page_id)
            path = '/api/v1/pages/'+quote(args.page_id,safe='')
            if args.revision_id:
                revision = await self.clients.call('knowledge','GET',path+'/revisions/'+quote(args.revision_id,safe=''),token)
                if revision.get('page_id') != args.page_id or revision.get('id') != args.revision_id:
                    raise fail('invalid_dependency_response')
            else:
                page = await self.clients.call('knowledge','GET',path,token)
                if page.get('space_id') != args.space_id or page.get('id') != args.page_id or not page.get('revision'):
                    raise fail('invalid_dependency_response')
                revision = page['revision']
            content = revision.get('content',{})
            references = content.get('evidence',[])
            for claim in content.get('claims',[]):
                references += claim.get('evidence',[])
            return await self.search_result(guard,run,{'items':[{'id':'page-'+digest([args.page_id,revision['id']]),'page_id':args.page_id,'revision_id':revision['id'],'space_id':args.space_id,'title':content['title'],'text':content['markdown'],'kind':'narrative','evidence':references}], 'degraded':[]},spaces,historical=bool(args.revision_id))
        if name == 'propose_revision':
            await guard.require('write',args.space_id,args.page_id)
            if not set(args.citation_ids) <= set(run['citations']):
                raise fail('unsupported_citation','Proposals require observed evidence',422)
            page = await self.clients.call('knowledge','GET','/api/v1/pages/'+quote(args.page_id,safe=''),token)
            if page.get('space_id') != args.space_id or page.get('id') != args.page_id:
                raise fail('invalid_dependency_response')
            if page.get('current_revision') != args.base_revision:
                raise fail('revision_conflict','The proposal base changed; a new explicit proposal is required',409)
            revision = page.get('revision')
            if not revision:
                raise fail('revision_conflict','Published target revision is required',409)
            dependency = {'space_id':args.space_id,'page_id':args.page_id,'revision_id':args.base_revision,'mode':'current','evidence':revision['content'].get('evidence',[])}
            refs = [run['citations'][cid]['evidence'] for cid in args.citation_ids]
            # Preserve canonical inherited access. Generated narrative is reviewed with the exact
            # observed sources, never transformed into automatic code or validity proof.
            content = {'title':args.title,'markdown':args.markdown,'entity_type':revision['content']['entity_type'],'claims':[], 'evidence':refs,'state':revision['content']['state'],'valid_from':revision['content'].get('valid_from'),'valid_until':revision['content'].get('valid_until')}
            dependencies = merge_dependencies(run['dependencies'],[dependency])
            await guard.dependencies(self.clients,dependencies)
            # Store the dependency BEFORE the model-generated write leaves the service; even an
            # uncertain response cannot later make its proposal-derived result less restricted.
            run['dependencies'] = dependencies
            await self.service.store.save(run)
            body = {'base_revision':args.base_revision,'content':content,'kind':args.kind,'reason':args.reason,'idempotency_key':'agent-'+digest([run['id'],call.id])}
            response = await self.clients.call('knowledge','POST','/api/v1/pages/'+quote(args.page_id,safe='')+'/proposals',token,body)
            if response.get('status') != 'pending' or response.get('page_id') != args.page_id or response.get('base_revision') != args.base_revision or not isinstance(response.get('id'),str):
                raise fail('invalid_proposal_response')
            await guard.dependencies(self.clients,dependencies)
            return {'result':{'proposal_id':response['id'],'status':'pending','published':False},'dependencies':[dependency],'citations':{},'fragment_ids':[]}
        if name == 'feedback':
            if 'knowledge:feedback' not in guard.scopes:
                raise fail('scope_required','Feedback requires its own OAuth scope',403)
            result = await self.service.feedback(token,FeedbackCreate(run_id=run['id'],rating=args.rating,comment=args.comment,idempotency_key='agent-'+digest([run['id'],call.id])))
            return {'result':result,'dependencies':[],'citations':{},'fragment_ids':[]}
        raise fail('tool_not_allowed','Tool is not permitted',403)

    async def search_result(self,guard,run,data,spaces,historical=False):
        items = data.get('items')
        if not isinstance(items,list) or len(items)>1000:
            raise fail('invalid_retrieval_response')
        dependencies, observed, references, fragment_ids = [], [], {}, []
        try:
            for item in items:
                if not isinstance(item,dict) or item.get('space_id') not in spaces:
                    raise fail('tool_scope_denied','Returned knowledge exceeds the effective scope',403)
                for field in ('id','page_id','revision_id','title','text'):
                    if not isinstance(item.get(field),str) or len(item[field])>(65536 if field=='text' else 2048):
                        raise ValueError
                if item.get('kind') not in {'fact','inference','gap','narrative'} or not isinstance(item.get('evidence'),list) or len(item['evidence'])>1000:
                    raise ValueError
                refs = [EvidenceRef.model_validate(ref).model_dump(mode='json') for ref in item['evidence']]
                for ref in refs:
                    references[digest(ref)] = {'evidence':ref,'page_id':item['page_id'],'revision_id':item['revision_id'],'space_id':item['space_id']}
                dependencies.append({'space_id':item['space_id'],'page_id':item['page_id'],'revision_id':item['revision_id'],'evidence':refs,'mode':'historical' if historical or run['request'].get('as_of') else 'current','as_of':run['request'].get('as_of')})
                observed.append({key:item[key] for key in ('id','page_id','revision_id','space_id','title','text','kind')})
                observed[-1]['citation_ids'] = [digest(ref) for ref in refs]
                fragment_ids.append(item['id'])
        except (ValidationError,ValueError,KeyError,TypeError):
            raise fail('invalid_retrieval_response') from None
        dependencies = merge_dependencies(dependencies)
        await guard.dependencies(self.clients,dependencies)
        citations = {}
        values = list(references.items())
        for offset in range(0,len(values),100):
            batch = values[offset:offset+100]
            response = await self.clients.call('knowledge','POST','/internal/v1/evidence/authorize',guard.token,{'evidence':[entry['evidence'] for _,entry in batch]})
            decisions = response.get('decisions')
            if not isinstance(decisions,list) or len(decisions)!=len(batch):
                raise fail('invalid_authorization')
            for (cid,entry),decision in zip(batch,decisions,strict=True):
                ref = entry['evidence']
                if decision.get('evidence')!=ref or decision.get('allowed') is not True or not isinstance(decision.get('excerpt'),str) or len(decision['excerpt'])>65536:
                    raise fail('evidence_unavailable','Original citation no longer available',403)
                citations[cid] = {'id':cid,**entry,'excerpt':decision['excerpt'],'url':'/citations/'+quote(ref['revision_id'],safe='')+'?start='+str(ref['start_line'])+'&end='+str(ref['end_line'])}
        await guard.dependencies(self.clients,dependencies)
        # Graph shape has already been validated by retrieval. Preserve only bounded IDs and
        # approved relation fields; never forward summaries or arbitrary upstream diagnostic text.
        graph = data.get('graph') or {'nodes':[],'edges':[],'paths':[]}
        safe_graph = {key:[] for key in ('nodes','edges','paths')}
        graph_fields = {'nodes':{'id','fragment_ids'},'edges':{'id','source','target','type','kind','fragment_ids'},'paths':{'node_ids','edge_ids','fragment_ids'}}
        for key,max_size in [('nodes',100),('edges',200),('paths',200)]:
            rows = graph.get(key,[])
            if not isinstance(rows,list) or len(rows)>max_size:
                raise fail('invalid_retrieval_response')
            for row in rows:
                if not isinstance(row,dict) or not set(row.get('fragment_ids',[]))<=set(fragment_ids):
                    raise fail('invalid_retrieval_response')
                safe_graph[key].append({field:deepcopy(value) for field,value in row.items() if field in graph_fields[key]})
        degraded = data.get('degraded',[])
        if not isinstance(degraded,list):
            raise fail('invalid_retrieval_response')
        safe_codes = [code for code in degraded if isinstance(code,str) and len(code)<=64 and all(c.isalnum() or c=='_' for c in code)][:20]
        result = {'items':observed,'citations':list(citations.values()),'graph':safe_graph,'degraded':safe_codes}
        if len(encoded(result))>self.service.settings.agent_max_context_chars:
            raise fail('context_budget_reached','Retrieved evidence exceeds the bounded model context',409)
        return {'result':result,'dependencies':dependencies,'citations':citations,'fragment_ids':fragment_ids}

    async def timeline_result(self,guard,run,data,spaces):
        items = data.get('items')
        if not isinstance(items,list) or len(items)>100:
            raise fail('invalid_timeline_response')
        dependencies,events = [],[]
        for item in items:
            if not isinstance(item,dict) or not all(isinstance(item.get(key),str) for key in ('page_id','revision_id','known_at','state','publication_kind')):
                raise fail('invalid_timeline_response')
            matches = []
            for space in spaces:
                if await guard.allowed('read',space,item['page_id']):
                    matches.append(space)
            if len(matches)!=1:
                raise fail('tool_scope_denied','Timeline page scope is ambiguous or unavailable',403)
            dependencies.append({'space_id':matches[0],'page_id':item['page_id'],'revision_id':item['revision_id'],'evidence':[],'mode':'historical','inspection':True,'as_of':run['request'].get('as_of')})
            events.append({key:deepcopy(value) for key,value in item.items() if key in {'page_id','revision_id','version','known_at','state','publication_kind','source_revisions'}})
        await guard.dependencies(self.clients,dependencies)
        return {'result':{'events':events,'truncated':data.get('truncated') is True,'evidence_gap':'Use get on exact revisions for citable original evidence; timeline metadata does not establish a production deployment.'},'dependencies':dependencies,'citations':{},'fragment_ids':[]}