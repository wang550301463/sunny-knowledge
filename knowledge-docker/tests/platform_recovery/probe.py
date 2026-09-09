#!/usr/bin/env python3
"""Private, container-only read probes and existing S3 backup-tool adapter."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

from prepare import DATABASES, RecoveryError, db_url, read_env, read_private, write_private

ROOT = Path('/recovery')


def validate_journal(rows, positive, negative):
    """Do not equate total partial counts with exact fixture coverage."""
    by_revision = {r['revision_id']: r for r in rows}
    if len(by_revision) != len(rows):
        raise RecoveryError('duplicate_journal_revision')
    positive_complete = all(by_revision.get(r, {}).get('outcome') == 'projected' for r in positive)
    negative_persisted = all(by_revision.get(r, {}).get('outcome') == 'not_visible' for r in negative)
    if not positive or not negative or not positive_complete or not negative_persisted:
        raise RecoveryError('fixture_journal_coverage_mismatch')
    return {'positive_complete': True, 'negative_persisted': True,
            'positive_revisions': len(positive), 'negative_revisions': len(negative)}


class Probe:
    def __init__(self):
        self.private = ROOT/'.local/recovery'
        self.plan = json.loads(read_private(self.private/'plan.json', self.private))
        self.values = read_env(ROOT/'.env')
        self.side = os.environ.get('RECOVERY_SIDE')
        if self.side not in {'source', 'target'} or self.values['COMPOSE_PROJECT_NAME'] != self.plan['target_project']:
            raise RecoveryError('invalid_probe_deployment')
        self.prefix = 'knowledge' if self.side == 'source' else 'recovery'

    def connect(self, service, *, projection=False):
        import psycopg
        database = ('projection_' if projection else self.prefix+'_')+service
        return psycopg.connect(db_url(service, database, self.values).replace('postgresql+psycopg://','postgresql://'),
                               connect_timeout=10, options='-c statement_timeout=30000 -c default_transaction_read_only=on')

    def fingerprints(self):
        from psycopg import sql
        result = {}
        for service in DATABASES:
            with self.connect(service) as connection:
                tables = connection.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2").fetchall()
                counts = {}
                for schema, table in tables:
                    counts[schema+'.'+table] = connection.execute(sql.SQL('SELECT count(*) FROM {}.{}').format(sql.Identifier(schema), sql.Identifier(table))).fetchone()[0]
                result[service] = counts
        return result

    def raw_module(self):
        spec = importlib.util.spec_from_file_location('recovery_raw_store', ROOT/'scripts/snapshot_store.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def s3(self):
        import boto3
        from botocore.config import Config
        return boto3.client('s3', endpoint_url='http://seaweedfs:8333', aws_access_key_id=self.values['S3_ACCESS_KEY'],
                            aws_secret_access_key=self.values['S3_SECRET_KEY'], region_name='us-east-1',
                            config=Config(connect_timeout=5,read_timeout=30,retries={'total_max_attempts':1},s3={'addressing_style':'path'}))

    def raw_backup(self):
        if self.side != 'source':
            raise RecoveryError('source_probe_required')
        module = self.raw_module()
        module.backup(self.s3(), 'knowledge-raw', self.private/'raw-backup')
        manifest = module.verify(self.private/'raw-backup')
        counts = self.fingerprints()
        write_private(self.private/'source-database-counts.json', json.dumps(counts, sort_keys=True))
        self.check_snapshot_keys(manifest)
        return {'object_count': len(manifest['objects']), 'database_count': len(counts)}

    def check_snapshot_keys(self, manifest):
        keys = {item['key'] for item in manifest['objects']}
        with self.connect('knowledge') as connection:
            references = connection.execute('SELECT DISTINCT object_key FROM knowledge_source_snapshots').fetchall()
        if any(row[0] not in keys for row in references):
            raise RecoveryError('canonical_snapshot_object_missing')

    def raw_restore(self):
        if self.side != 'target':
            raise RecoveryError('target_probe_required')
        module, client, bucket = self.raw_module(), self.s3(), self.plan['target_bucket']
        # A previous/uncertain bucket requires investigation; do not treat an old restore as fresh.
        buckets = client.list_buckets()['Buckets']
        if any(row['Name'] == bucket for row in buckets):
            raise RecoveryError('new_target_bucket_required')
        client.create_bucket(Bucket=bucket)
        module.restore(client, bucket, self.private/'raw-backup')
        manifest = module.verify(self.private/'raw-backup')
        original = json.loads(read_private(self.private/'source-database-counts.json', self.private))
        if self.fingerprints() != original or set(original) != set(DATABASES):
            raise RecoveryError('restored_database_table_counts_mismatch')
        self.check_snapshot_keys(manifest)
        return {'object_count': len(manifest['objects']), 'database_count': len(original)}

    def fresh(self):
        if self.side != 'target':
            raise RecoveryError('target_probe_required')
        import httpx
        from neo4j import GraphDatabase
        catalogs = {}
        for service in ('retrieval','graphiti'):
            with self.connect(service, projection=True) as connection:
                catalogs['projection_'+service] = connection.execute("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')").fetchone()[0]
        response = httpx.head('http://elasticsearch:9200/'+self.plan['target_es_index'],timeout=10)
        if response.status_code not in {200,404}:
            raise RecoveryError('es_freshness_unknown')
        with GraphDatabase.driver('bolt://neo4j:7687', auth=('neo4j',self.values['NEO4J_PASSWORD']), connection_timeout=10) as driver:
            with driver.session(database='neo4j',default_access_mode='READ') as session:
                nodes = session.run('MATCH (n) WHERE n.knowledge_namespace=$namespace RETURN count(n) AS count',namespace=self.plan['target_graph_namespace']).single()['count']
                edges = session.run('MATCH ()-[r]-() WHERE r.knowledge_namespace=$namespace RETURN count(r) AS count',namespace=self.plan['target_graph_namespace']).single()['count']
        return {'catalog_tables':catalogs,'es_index_exists':response.status_code==200,'graph_nodes':nodes,'graph_edges':edges}

    def verify_journals(self):
        if self.side != 'target':
            raise RecoveryError('target_probe_required')
        from psycopg.rows import dict_row
        from psycopg import sql
        baseline = json.loads(read_private(self.private/'fixture.json', self.private))
        rebuild = json.loads(read_private(self.private/'receipts/rebuild.complete.json', self.private))
        if baseline['recovery_id'] != self.plan['recovery_id'] or baseline['state'] != 'complete':
            raise RecoveryError('source_baseline_not_complete')
        positive = {p['revision']['id'] for p in baseline['positive']['pages'].values()}
        negative = {p['revision']['id'] for p in baseline['negative']['pages'].values()}
        for service in ('retrieval','graphiti'):
            run_id = rebuild['workers'][service]['run_id']
            with self.connect(service, projection=True) as connection:
                connection.row_factory = dict_row
                rows = connection.execute(sql.SQL('SELECT revision_id,outcome FROM {} WHERE run_id=%s').format(sql.Identifier(service+'_rebuild_items')),(run_id,)).fetchall()
                validate_journal(rows, positive, negative)
            if rebuild['workers'][service]['inventory_state'] != 'partial':
                raise RecoveryError('mixed_acl_partial_result_required')
        # Rebuild must not consume/ACK canonical outbox deliveries. Compare exact baseline timestamps.
        with self.connect('knowledge') as connection:
            current = connection.execute('SELECT o.revision_id,d.consumer,d.acked_at::text FROM knowledge_outbox o LEFT JOIN knowledge_deliveries d ON d.event_id=o.id WHERE o.revision_id=ANY(%s) ORDER BY 1,2', (sorted(positive|negative),)).fetchall()
        if [list(row) for row in current] != baseline['delivery_acknowledgements']:
            raise RecoveryError('rebuild_changed_canonical_acknowledgements')
        return {'positive_complete':True,'negative_persisted':True,'positive_revisions':len(positive),'negative_revisions':len(negative),
                'canonical_acknowledgements_unchanged':True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['raw-backup','raw-restore','fresh','verify-journals'])
    args = parser.parse_args()
    try:
        result = getattr(Probe(),args.action.replace('-','_'))()
        print(json.dumps(result))
        return 0
    except Exception:
        print(json.dumps({'state':'failed','error':'recovery_probe_failed'}))
        return 1


if __name__=='__main__':
    sys.exit(main())