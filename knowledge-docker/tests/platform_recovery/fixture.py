#!/usr/bin/env python3
"""Real PKCE/API fixture before/after recovery. Public three-language input only."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import quote

import httpx

ROOT = Path('/recovery')
sys.path[:0] = [str(ROOT/'scripts'),str(ROOT/'tests/git_graph_http'),str(ROOT/'tests/platform_recovery')]
from oidc_login import http_client
from prepare import RecoveryError, read_private, write_private
from probe import Probe
from test_git_graph_flow import GitGraphHTTPRegression


class Fixture(GitGraphHTTPRegression):
    def setup(self):
        self.probe = Probe()
        self.private = self.probe.private
        self.plan = self.probe.plan
        self.config = json.loads(read_private(ROOT/'.local/test-env.json',ROOT))
        if self.config['public_url'] != 'http://localhost:28181':
            raise RecoveryError('fixture_origin_not_authorized')
        self.fixture_url = 'http://git-fixture:8080'
        response = httpx.get(self.fixture_url+'/manifest.json',timeout=10)
        self.assertEqual(response.status_code,200)
        self.manifest = response.json()
        self.assertEqual(self.manifest['kind'],'fixed_three_language_git_fixture')
        self.snapshots = {}
        self.assets_path = self.private/('assets-'+self.probe.side+'.json')
        self.assets = {'recovery_id':self.plan['recovery_id'],'spaces':[], 'model':None}

    def save_assets(self):
        write_private(self.assets_path,json.dumps(self.assets),replace=self.assets_path.exists())

    def before(self):
        if self.probe.side != 'source' or (self.private/'fixture.started.json').exists():
            raise RecoveryError('fresh_source_fixture_required')
        write_private(self.private/'fixture.started.json',json.dumps({'recovery_id':self.plan['recovery_id'],'state':'started'}))
        self.save_assets()
        me = self.call('GET','/me')
        self.assertIn('platform_admin',me['permissions'])
        actor = 'user:'+me['id']
        workers = {s:'service:'+self.config[s+'_principal_id'] for s in ('ingest','retrieval','graphiti')}
        dataset = {}
        for label in ('positive','negative'):
            space = self.call('POST','/spaces',{'name':'recovery-'+label+'-'+self.plan['recovery_id']},201)['id']
            owned = {'id':space,'sources':[]};self.assets['spaces'].append(owned);self.save_assets()
            for action,subjects in (('read',[actor,*workers.values()]),('write',[actor,workers['ingest']])):
                self.call('PUT','/grants',{'space_id':space,'action':action,'subjects':subjects})
            source = self.call('POST','/sources',{'name':'Recovery public Java TS Go '+label,'kind':'git','space_id':space,
                'config':{'url':self.fixture_url+self.manifest['repository_path'],'ref':self.manifest['refs']['v1']}},201)
            owned['sources'].append(source['resource_id']);self.save_assets()
            task = self.sync('/sources/'+source['id'],'v1',1)
            pages = self.published_pages(task,'v1')
            graph = self.verify_dependency_paths(space,pages,'v1')
            self.verify_search(space,pages['java/pom.xml'],'org.slf4j:slf4j-api','v1')
            dataset[label] = {'space_id':space,'source':source,'task_id':task['id'],'pages':pages,'graph':graph}
        revisions = sorted({p['revision']['id'] for group in dataset.values() for p in group['pages'].values()})
        deadline = time.monotonic()+120
        acknowledgements = []
        while time.monotonic()<deadline:
            with self.probe.connect('knowledge') as connection:
                acknowledgements = connection.execute('SELECT o.revision_id,d.consumer,d.acked_at::text FROM knowledge_outbox o LEFT JOIN knowledge_deliveries d ON d.event_id=o.id WHERE o.revision_id=ANY(%s) ORDER BY 1,2',(revisions,)).fetchall()
            if len(acknowledgements)==len(revisions)*2 and all(row[2] for row in acknowledgements):
                break
            time.sleep(1)
        self.assertEqual(len(acknowledgements),len(revisions)*2)
        self.assertTrue(all(row[2] for row in acknowledgements),'Both real projection workers must ACK before backup')
        denied = dataset['negative']
        self.call('PUT','/grants',{'space_id':denied['space_id'],'resource_id':denied['source']['resource_id'],
                                 'action':'read','subjects':[actor]})
        # Keep actor visibility but revoke machine projections. No worker receives global access.
        self.call('GET','/pages/'+quote(denied['pages']['java/pom.xml']['id'],safe=''))
        credential = 'recovery-canary-'+secrets.token_hex(24)
        model = self.call('POST','/models',{'name':'Recovery encrypted protocol canary '+self.plan['recovery_id'],
            'provider':'openai','provider_model':'protocol-fixture-embedding','capability':'embedding',
            'base_url':'http://git-model-provider:8080/v1','dimensions':8,'request_dimensions':True,
            'max_input_chars':8192,'max_batch_size':10,'timeout_seconds':15,'max_retries':0,
            'credential':credential},201)
        self.assets['model']=model;self.save_assets()
        self.check_model(model)
        canary = {'model':model,'credential_sha256':hashlib.sha256(credential.encode()).hexdigest()}
        canary['ciphertext_sha256'] = self.check_cipher(canary)
        baseline = {'state':'complete','recovery_id':self.plan['recovery_id'], 'model_kind':'deterministic_protocol_simulation',
                    'actor':actor,'workers':workers,'manifest':self.manifest,'snapshots':self.snapshots,
                    'delivery_acknowledgements':[list(row) for row in acknowledgements], 'canary':canary, **dataset}
        write_private(self.private/'fixture.json',json.dumps(baseline,sort_keys=True))
        return {'state':'complete','spaces':2,'languages':3,'revisions':len(revisions),'negative_worker_acl_revoked':True}

    def check_model(self,model):
        result = self.call('POST','/models/'+model['id']+'/test',{'configuration_id':model['configuration_id']})
        self.assertEqual(result['test_state'],'passed')

    def check_cipher(self,canary):
        from knowledge_platform.common.secrets import SecretBox
        with self.probe.connect('llm') as connection:
            row=connection.execute('SELECT credential_ciphertext FROM llm_configurations WHERE id=%s',(canary['model']['configuration_id'],)).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(row[0])
        plaintext = SecretBox(self.probe.values['CREDENTIAL_ENCRYPTION_KEY']).decrypt(row[0],canary['model']['configuration_id'])
        self.assertEqual(hashlib.sha256(plaintext.encode()).hexdigest(),canary['credential_sha256'])
        with self.assertRaises(ValueError):
            SecretBox(base64.b64encode(secrets.token_bytes(32)).decode()).decrypt(row[0],canary['model']['configuration_id'])
        return hashlib.sha256(row[0].encode()).hexdigest()

    def baseline(self):
        baseline = json.loads(read_private(self.private/'fixture.json',self.private))
        if baseline['recovery_id'] != self.plan['recovery_id'] or baseline['state'] != 'complete':
            raise RecoveryError('source_fixture_incomplete')
        return baseline

    def after(self):
        if self.probe.side != 'target' or (self.private/'target-api.complete.json').exists():
            raise RecoveryError('fresh_target_verification_required')
        baseline = self.baseline()
        self.assertEqual(self.manifest,baseline['manifest'])
        me = self.call('GET','/me')
        self.assertEqual('user:'+me['id'],baseline['actor'])
        positive = baseline['positive']
        self.snapshots = {}  # Re-fetch exact sources from restored DB, not the saved response.
        for group in (positive,baseline['negative']):
            for old in group['pages'].values():
                page = self.call('GET','/pages/'+quote(old['id'],safe=''))
                self.assertEqual(page['revision'],old['revision'])
                content=page['revision']['content']
                for ref in content['evidence']+[r for claim in content['claims'] for r in claim['evidence']]:
                    self.check_ref(ref,'v1')
        self.assertEqual(self.snapshots,baseline['snapshots'])
        graph=self.verify_dependency_paths(positive['space_id'],positive['pages'],'v1')
        triples=lambda edges:{(e['source'],e['target'],e['type']) for e in edges}
        self.assertEqual(triples(graph['edges']),triples(positive['graph']['edges']))
        self.verify_search(positive['space_id'],positive['pages']['java/pom.xml'],'org.slf4j:slf4j-api','v1')
        denied=baseline['negative']
        result=self.call('POST','/traverse',{'space_ids':[denied['space_id']],'seed_fragment_ids':denied['graph']['seeds'],
                        'relation':{'types':['depends_on'],'direction':'outgoing','hops':1}})
        self.assertEqual(result['items'],[])
        self.assertEqual(result['graph']['edges'],[])
        self.check_model(baseline['canary']['model'])
        self.assertEqual(self.check_cipher(baseline['canary']),baseline['canary']['ciphertext_sha256'])
        result={'state':'complete','recovery_id':self.plan['recovery_id'],'positive_revisions':len(positive['pages']),
                'canonical_and_sources_equal':True,'encrypted_configuration_restored':True,'same_origin_pkce_passed':True,
                'positive_graph_and_search_passed':True,'negative_projection_absent':True,'model_kind':'deterministic_protocol_simulation'}
        write_private(self.private/'target-api.complete.json',json.dumps(result))
        return {k:v for k,v in result.items() if k!='recovery_id'}

    def revoke(self):
        baseline=self.baseline();positive=baseline['positive']
        self.call('PUT','/grants',{'space_id':positive['space_id'],'resource_id':positive['source']['resource_id'],
            'action':'read','subjects':list(baseline['workers'].values())})
        page=positive['pages']['java/pom.xml']
        for path in ('/sources/'+positive['source']['id'],'/pages/'+quote(page['id'],safe=''),
                     '/pages/'+quote(page['id'],safe='')+'/revisions/'+page['revision']['id'],
                     '/source-snapshots/'+next(iter(baseline['snapshots']))):
            # Pick a positive snapshot, never a negative source still visible to the actor.
            if path.startswith('/source-snapshots/'):
                ref=page['revision']['content']['claims'][0]['evidence'][0]
                path='/source-snapshots/'+ref['revision_id']
            self.call('GET',path,want=403)
        for path,request in (('/search',{'space_ids':[positive['space_id']],'query':'org.slf4j:slf4j-api'}),
            ('/traverse',{'space_ids':[positive['space_id']],'seed_fragment_ids':positive['graph']['seeds'],
                         'relation':{'types':['depends_on'],'direction':'outgoing','hops':1}})):
            result=self.call('POST',path,request)
            for name in ('items','evidence'):self.assertEqual(result[name],[])
            for name in ('nodes','edges'):self.assertEqual(result['graph'][name],[])
        result={'state':'complete','recovery_id':self.plan['recovery_id'],'revocation_passed':True,'side':self.probe.side}
        write_private(self.private/(self.probe.side+'-revocation.complete.json'),json.dumps(result))
        return {'state':'complete','revocation_passed':True}

    def cleanup(self):
        # Source records are copied by restore: same exact identifiers are owned on both sides.
        assets=json.loads(read_private(self.private/'assets-source.json',self.private))
        if assets['recovery_id']!=self.plan['recovery_id']:raise RecoveryError('asset_owner_mismatch')
        confirmed=[]
        for space in assets['spaces']:
            for resource in space['sources']:
                self.call('PUT','/grants',{'space_id':space['id'],'resource_id':resource,'action':'read','subjects':[]})
            for action in ('write','read'):
                self.call('PUT','/grants',{'space_id':space['id'],'action':action,'subjects':[]})
            confirmed.append(space['id'])
        if assets['model']:
            model=self.call('GET','/models/'+assets['model']['id'])
            if model['state']!='retired':
                self.call('DELETE','/models/'+model['id']+'?base_configuration_id='+model['configuration_id'])
        result={'state':'complete','recovery_id':self.plan['recovery_id'],'spaces':len(confirmed),'model_retired':bool(assets['model'])}
        write_private(self.private/(self.probe.side+'-cleanup.complete.json'),json.dumps(result))
        return {'state':'complete','spaces':len(confirmed)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['before','after','revoke','cleanup'])
    args=parser.parse_args()
    try:
        fixture=Fixture();fixture.setup()
        with http_client(fixture.config['public_url'],base_url=fixture.config['public_url'],timeout=30) as fixture.client:
            fixture.refresh()
            result=getattr(fixture,args.phase)()
        print(json.dumps(result))
        return 0
    except Exception:
        # Assertions may contain full page diffs; keep them out of stdout/logs.
        print(json.dumps({'state':'failed_or_uncertain','error':'recovery_api_fixture_failed'}))
        return 1


if __name__=='__main__':
    raise SystemExit(main())