"""Canonical revision flow through the real gateway/auth/IAM/knowledge containers."""
import json
import unittest
import uuid

import httpx

from test_identity import ROOT, login_module


class KnowledgeRegression(unittest.TestCase):
    def test_review_cas_history_rollback_and_live_acl_revocation(self):
        config=json.loads((ROOT/'.local/test-env.json').read_text())
        base=config['public_url']
        tokens=login_module.login(base,config['admin_username'],config['admin_password'])
        with httpx.Client(base_url=base,headers={'Authorization':'Bearer '+tokens['access_token']},timeout=20) as client:
            response=client.post('/api/v1/spaces',json={'name':'wiki-regression-'+str(uuid.uuid4())})
            self.assertEqual(response.status_code,201,response.text)
            space=response.json()['id']
            content={'title':'回滚流程','markdown':'人工维护：先检查告警，再按审核流程回滚。'}
            response=client.post('/api/v1/pages',json={'space_id':space,'content':content})
            self.assertEqual(response.status_code,201,response.text)
            result=response.json();page=result['page']['id'];proposal=result['proposal']['id']
            response=client.post('/api/v1/reviews/'+proposal+'/approve',json={'reason':'真实容器回归：审核发布'})
            self.assertEqual(response.status_code,200,response.text)
            first=response.json()['id']
            changed=content|{'markdown':'人工维护：先检查告警、确认影响，再按审核流程回滚。'}
            response=client.post('/api/v1/pages/'+page+'/proposals',json={'base_revision':first,'content':changed,'reason':'补充影响检查'})
            self.assertEqual(response.status_code,201,response.text)
            second_proposal=response.json()['id']
            response=client.post('/api/v1/reviews/'+second_proposal+'/approve',json={'reason':'审核通过'})
            self.assertEqual(response.status_code,200,response.text)
            second=response.json()['id']
            response=client.post('/api/v1/pages/'+page+'/proposals',json={'base_revision':first,'content':content,'reason':'过期写入应拒绝'})
            self.assertEqual(response.status_code,409,response.text)
            response=client.get('/api/v1/pages/'+page+'/revisions/'+first)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['content']['markdown'],content['markdown'])
            response=client.post('/api/v1/pages/'+page+'/rollback',json={'base_revision':second,'revision_id':first,'reason':'验证回滚生成新版本'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertNotIn(response.json()['id'],[first,second])
            response=client.put('/api/v1/grants',json={'space_id':space,'resource_id':page,'action':'read','subjects':[]})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(client.get('/api/v1/pages/'+page).status_code,403)
            self.assertEqual(client.get('/api/v1/pages/'+page+'/revisions/'+first).status_code,403)
            response=client.get('/api/v1/pages',params={'space_id':space})
            self.assertEqual(response.status_code,200,response.text)
            self.assertNotIn(page,[item['id'] for item in response.json()['items']])

if __name__=='__main__': unittest.main()