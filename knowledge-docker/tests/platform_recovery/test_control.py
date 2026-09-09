"""Independent stage safety and recovery completeness checks; never contact Docker."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).parent))


class RecoveryControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('recovery_control',Path(__file__).with_name('control.py'))
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)

    def test_only_exact_source_inventory_is_accepted_and_no_private_environment_is_captured(self):
        m=self.module
        raw=[{'Id':'c1','Image':'sha256:abc','Name':'/source-postgres','Config':{'Labels':{
            'com.docker.compose.project':m.SOURCE_PROJECT,'com.docker.compose.service':'postgres'},'Env':['SECRET=do-not-log']},
            'State':{'Running':True},'NetworkSettings':{'Networks':{'source_backend':{'NetworkID':'n1'}}}}]
        clean=m.sanitize_inventory(raw,m.SOURCE_PROJECT)
        self.assertNotIn('do-not-log',json.dumps(clean))
        self.assertEqual(clean[0]['image'],'sha256:abc')
        raw[0]['Config']['Labels']['com.docker.compose.project']='sunny-main'
        with self.assertRaises(m.RecoveryError):m.sanitize_inventory(raw,m.SOURCE_PROJECT)

    def test_quiescence_requires_same_pg_s3_ids_and_all_other_services_stopped(self):
        m=self.module
        base=[{'id':'pg','service':'postgres','running':True},{'id':'s3','service':'seaweedfs','running':True},
              {'id':'writer','service':'agent-worker','running':True}]
        stopped=[dict(v,running=v['service'] in {'postgres','seaweedfs'}) for v in base]
        m.check_quiescence(base,stopped,True)
        for current,external in ((base,True),(stopped,False),(stopped+[{'id':'other','service':'gateway','running':True}],True),
                                 ([dict(v,id='changed') if v['service']=='postgres' else v for v in stopped],True)):
            with self.subTest(current=current,external=external),self.assertRaises(m.RecoveryError):
                m.check_quiescence(base,current,external)

    def test_interrupted_or_failed_stage_cannot_be_treated_as_done_or_blindly_restarted(self):
        m=self.module
        with tempfile.TemporaryDirectory() as temporary:
            journal=m.Journal(Path(temporary),'f'*32)
            with self.assertRaises(RuntimeError):
                with journal.stage('backup',[]):raise RuntimeError('provider secret must not persist')
            self.assertNotIn('provider secret', ''.join(p.read_text() for p in Path(temporary).glob('*.json')))
            with self.assertRaises(m.RecoveryError):journal.require('backup')
            with self.assertRaises(m.RecoveryError):
                with journal.stage('backup',[]):pass
            with journal.stage('capture',[]) as result:result['count']=3
            self.assertEqual(journal.require('capture')['count'],3)
            Path(temporary,'quiesced.started.json').write_text('{}')
            with self.assertRaises(m.RecoveryError):
                with journal.stage('quiesced',['capture']):pass

    def test_fresh_targets_reject_prior_catalog_tables_any_index_or_graph_projection(self):
        m=self.module
        good={'catalog_tables':{'projection_retrieval':0,'projection_graphiti':0},'es_index_exists':False,
              'graph_nodes':0,'graph_edges':0}
        m.check_fresh(good)
        for bad in ({'catalog_tables':{'projection_retrieval':1,'projection_graphiti':0}},
                    {'es_index_exists':True},{'graph_nodes':1},{'graph_edges':1}, {'graph_edges':None}):
            with self.subTest(bad=bad),self.assertRaises(m.RecoveryError):m.check_fresh(dict(good,**bad))

    def test_partial_is_separate_from_authorized_fixture_coverage_and_requires_persisted_deny(self):
        m=self.module
        report={'status':'partial','projected':8,'not_visible':2,'total':10,'run_id':'a'}
        self.assertEqual(m.classify_rebuild(2,report)['inventory_state'],'partial')
        for code,bad in ((0,report),(2,dict(report,not_visible=0)),(1,report),
                         (2,dict(report,total=99)),(2,dict(report,status='complete'))):
            with self.subTest(code=code,bad=bad),self.assertRaises(m.RecoveryError):m.classify_rebuild(code,bad)
        self.assertEqual(m.classify_rebuild(0,dict(report,status='complete',not_visible=0,total=8))['inventory_state'],'complete')


if __name__=='__main__':unittest.main()