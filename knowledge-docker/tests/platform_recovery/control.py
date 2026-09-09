#!/usr/bin/env python3
"""Operator-invoked recovery phases. Never stop, start, delete or reconfigure a service."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

from prepare import DATABASES, RecoveryError, SOURCE_PROJECT, read_env, read_private, write_private

STAGES = {'capture', 'quiesced', 'backup', 'restore', 'fresh', 'rebuild', 'verified'}
INFRA = {'postgres', 'seaweedfs', 'elasticsearch', 'neo4j', 'valkey'}


def sanitize_inventory(raw, project):
    result = []
    for row in raw:
        labels = row['Config']['Labels']
        if labels.get('com.docker.compose.project') != project:
            raise RecoveryError('container_project_mismatch')
        result.append({'id': row['Id'], 'service': labels['com.docker.compose.service'],
                       'image': row['Image'], 'running': row['State']['Running'],
                       'networks': {name: value['NetworkID'] for name, value in row['NetworkSettings']['Networks'].items()}})
    return result


def check_quiescence(before, current, external_writers_stopped):
    if not external_writers_stopped:
        raise RecoveryError('external_writers_not_attested')
    for service in ('postgres', 'seaweedfs'):
        original = [r['id'] for r in before if r['service'] == service and r['running']]
        present = [r['id'] for r in current if r['service'] == service and r['running']]
        if len(original) != 1 or original != present:
            raise RecoveryError('source_storage_identity_changed')
    if any(r['running'] and r['service'] not in {'postgres', 'seaweedfs'} for r in current):
        raise RecoveryError('source_writers_still_running')


def check_fresh(value):
    if value.get('catalog_tables') != {'projection_retrieval': 0, 'projection_graphiti': 0}:
        raise RecoveryError('fresh_catalog_required')
    if value.get('es_index_exists') is not False:
        raise RecoveryError('new_absent_es_index_required')
    if any(type(value.get(k)) is not int or value[k] != 0 for k in ('graph_nodes', 'graph_edges')):
        raise RecoveryError('empty_graph_namespace_required')


def classify_rebuild(code, value):
    counts = [value.get(k) for k in ('projected', 'not_visible', 'processed')]
    if any(type(v) is not int or v < 0 for v in counts) or sum(counts[:2]) != counts[2]:
        raise RecoveryError('invalid_rebuild_counts')
    partial = value.get('status') == 'partial'
    if (value.get('status') not in {'partial', 'complete'} or value.get('scan_complete') is not True
            or value.get('full_rebuild_complete') is not (not partial)
            or (partial and (code != 2 or value['not_visible'] == 0))
            or (not partial and (code != 0 or value['not_visible'] != 0))):
        raise RecoveryError('unconfirmed_rebuild')
    return {'inventory_state': value['status'], 'projected': counts[0], 'not_visible': counts[1],
            'processed': counts[2], 'run_id': value['run_id']}


class Journal:
    """Exclusive receipts preserve interruptions; failed/unknown mutations require investigation."""
    def __init__(self, directory, recovery_id):
        self.directory, self.recovery_id = Path(directory), recovery_id
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def require(self, name):
        if name not in STAGES:
            raise RecoveryError('invalid_stage')
        try:
            result = json.loads(read_private(self.directory / (name+'.complete.json'), self.directory))
        except (OSError, ValueError):
            raise RecoveryError('prerequisite_not_complete') from None
        if result.get('state') != 'complete' or result.get('recovery_id') != self.recovery_id:
            raise RecoveryError('prerequisite_not_complete')
        return result

    @contextmanager
    def stage(self, name, prerequisites):
        if name not in STAGES:
            raise RecoveryError('invalid_stage')
        for prerequisite in prerequisites:
            self.require(prerequisite)
        if any(self.directory.glob(name+'.*.json')):
            raise RecoveryError('stage_already_attempted_inspect_receipts')
        base = {'stage': name, 'recovery_id': self.recovery_id, 'started_at': int(time.time())}
        write_private(self.directory / (name+'.started.json'), json.dumps(dict(base, state='started')))
        result = dict(base)
        try:
            yield result
            write_private(self.directory / (name+'.complete.json'), json.dumps(dict(result, state='complete', finished_at=int(time.time())), indent=2))
        except BaseException:
            # Never serialize exceptions: database/provider errors can contain credentials or source text.
            write_private(self.directory / (name+'.failed.json'), json.dumps(dict(base, state='failed_or_uncertain', error='phase_not_confirmed')))
            raise


def command(arguments, *, env=None, timeout=3600, accepted=(0,)):
    result = subprocess.run(arguments, env=env, capture_output=True, timeout=timeout, check=False)
    if result.returncode not in accepted:
        raise RecoveryError('command_failed_or_uncertain')
    return result.returncode, result.stdout


class Controller:
    def __init__(self, plan_file):
        self.plan_file = Path(plan_file).absolute()
        self.private = self.plan_file.parent
        self.plan = json.loads(read_private(self.plan_file, self.private))
        self.target = Path(self.plan['target_root'])
        self.source = Path(self.plan['source_root'])
        self.values = read_env(self.target/'.env')
        if (self.plan['source_project'] != SOURCE_PROJECT or self.plan['public_url'] != 'http://localhost:28181'
                or self.plan['target_project'] != 'sunny-recovery-'+self.plan['recovery_id'][:12]
                or self.values.get('COMPOSE_PROJECT_NAME') != self.plan['target_project']
                or self.values.get('PUBLIC_WEB_URL') != self.plan['public_url']
                or self.values.get('GATEWAY_PORT') != '28181'):
            raise RecoveryError('deployment_identity_mismatch')
        self.journal = Journal(self.private/'receipts', self.plan['recovery_id'])

    def inventory(self, project):
        if project not in {SOURCE_PROJECT, self.plan['target_project']}:
            raise RecoveryError('project_not_authorized')
        _, value = command(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project='+project], timeout=15)
        ids = value.decode().split()
        if not ids:
            raise RecoveryError('project_missing')
        _, value = command(['docker', 'inspect', *ids], timeout=15)
        return sanitize_inventory(json.loads(value), project)

    def one(self, rows, service):
        matches = [row for row in rows if row['service'] == service and row['running']]
        if len(matches) != 1:
            raise RecoveryError('single_running_service_required')
        return matches[0]

    def quiescent(self):
        before = self.journal.require('capture')['inventory']
        current = self.inventory(SOURCE_PROJECT)
        check_quiescence(before, current, self.journal.require('quiesced')['external_writers_stopped'])
        return current

    def capture(self):
        with self.journal.stage('capture', []) as receipt:
            rows = self.inventory(SOURCE_PROJECT)
            for service in ('postgres', 'seaweedfs', 'gateway', 'knowledge', 'retrieval', 'graphiti'):
                self.one(rows, service)
            receipt['inventory'] = rows
            # Docker image IDs are immutable. No image pulling/building occurs in this harness.
            images = {}
            for row in rows:
                if row['running']:
                    if row['service'] in images:
                        raise RecoveryError('replicated_service_requires_operator_mapping')
                    images[row['service']] = {'image': row['image']}
            _, raw = command(['docker', 'image', 'inspect', 'sunny-knowledge/regression:v2-local', '--format', '{{.Id}}'], timeout=15)
            receipt['tool_image'] = raw.decode().strip()
            if not receipt['tool_image'].startswith('sha256:'):
                raise RecoveryError('immutable_tool_image_required')
            write_private(self.target/'compose.recovery-images.json', json.dumps({'services': images}, indent=2))

    def quiesced(self, attestation):
        with self.journal.stage('quiesced', ['capture']) as receipt:
            check_quiescence(self.journal.require('capture')['inventory'], self.inventory(SOURCE_PROJECT), attestation)
            receipt['external_writers_stopped'] = attestation
            receipt['consistency'] = 'operator_quiesced_cross_service_window'

    def pg(self, side, rows):
        specification = importlib.util.spec_from_file_location('recovery_pg_store', self.target/'scripts/postgres_store.py')
        module = importlib.util.module_from_spec(specification); specification.loader.exec_module(module)
        config = module.private_json(self.private/(side+'-pg.json'))
        return module, module.PostgresTools(config['connection'], docker_container=self.one(rows, 'postgres')['id']), config

    def probe(self, side, action):
        project = SOURCE_PROJECT if side == 'source' else self.plan['target_project']
        rows = self.inventory(project)
        storage = self.one(rows, 'postgres')
        networks = storage['networks']
        candidates = [name for name in networks if name.endswith('_backend')]
        if len(candidates) != 1:
            raise RecoveryError('single_backend_network_required')
        env = dict(os.environ)
        env['RECOVERY_SIDE'] = side
        argv = ['docker', 'run', '--rm', '--network', candidates[0], '--user', str(os.getuid())+':'+str(os.getgid()),
                '--read-only', '--tmpfs', '/tmp', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                '--env', 'RECOVERY_SIDE', '--mount', 'type=bind,src='+str(self.target)+',dst=/recovery',
                '--workdir', '/recovery', self.journal.require('capture')['tool_image'],
                'python', 'tests/platform_recovery/probe.py', action]
        _, output = command(argv, env=env)
        try:
            return json.loads(output)
        except ValueError:
            raise RecoveryError('invalid_probe_receipt') from None

    def backup(self):
        with self.journal.stage('backup', ['quiesced']) as receipt:
            rows = self.quiescent()
            module, pg, config = self.pg('source', rows)
            directory = self.private/'postgres-backup'
            module.backup(pg, module.records(config['databases']), directory)
            module.verify(pg, directory)
            self.probe('source', 'raw-backup')
            self.quiescent()
            receipt.update(database_count=len(config['databases']), raw_verified=True)

    def restore(self):
        with self.journal.stage('restore', ['backup']) as receipt:
            self.quiescent()
            rows = self.inventory(self.plan['target_project'])
            if any(row['running'] and row['service'] not in INFRA for row in rows):
                raise RecoveryError('target_application_started_before_restore')
            module, pg, config = self.pg('target', rows)
            module.restore(pg, self.private/'postgres-backup', module.records(config['databases']), self.private/'postgres-restored.json')
            raw = self.probe('target', 'raw-restore')
            receipt.update(database_count=len(config['databases']), raw_verified=True, object_count=raw['object_count'])
            self.quiescent()

    def fresh(self):
        with self.journal.stage('fresh', ['restore']) as receipt:
            self.quiescent()
            rows = self.inventory(self.plan['target_project'])
            if any(row['running'] and row['service'] not in INFRA for row in rows):
                raise RecoveryError('fresh_check_must_precede_applications')
            result = self.probe('target', 'fresh')
            check_fresh(result)
            receipt.update(result)
            receipt['storage_ids'] = {s:self.one(rows, s)['id'] for s in ('postgres', 'elasticsearch', 'neo4j', 'seaweedfs')}

    def rebuild(self):
        with self.journal.stage('rebuild', ['fresh']) as receipt:
            self.quiescent()
            rows = self.inventory(self.plan['target_project'])
            for service, identity in self.journal.require('fresh')['storage_ids'].items():
                if self.one(rows, service)['id'] != identity:
                    raise RecoveryError('physical_target_replaced_requires_new_plan')
            if any(r['running'] and r['service'].endswith('-worker') for r in rows):
                raise RecoveryError('background_workers_must_remain_stopped_during_rebuild')
            results = {}
            for service in ('retrieval', 'graphiti'):
                argv = ['docker', 'compose', '--project-directory', str(self.target), '--env-file', str(self.target/'.env'),
                        '-p', self.plan['target_project'], '-f', str(self.target/'compose.yaml'),
                        '-f', str(self.target/'compose.graph-regression.yaml'), '-f', str(self.target/'compose.recovery-images.json'),
                        '-f', str(self.target/'compose.recovery.json'), 'run', '--rm', '--no-deps', service+'-worker',
                        'python', '-m', 'knowledge_platform.'+service+'.worker', '--rebuild']
                code, raw = command(argv, timeout=3600, accepted=(0, 2))
                parsed = [json.loads(line) for line in raw.decode().splitlines() if line.startswith('{')]
                if len(parsed) != 1:
                    raise RecoveryError('invalid_rebuild_receipt')
                results[service] = classify_rebuild(code, parsed[0])
            receipt['workers'] = results
            receipt['inventory_state'] = 'partial' if any(v['inventory_state'] == 'partial' for v in results.values()) else 'complete'
            self.quiescent()

    def verified(self):
        with self.journal.stage('verified', ['rebuild']) as receipt:
            result = self.probe('target', 'verify-journals')
            if result.get('positive_complete') is not True or result.get('negative_persisted') is not True:
                raise RecoveryError('fixture_projection_coverage_not_proven')
            api = json.loads(read_private(self.private/'target-api.complete.json', self.private))
            if api.get('recovery_id') != self.plan['recovery_id'] or api.get('state') != 'complete':
                raise RecoveryError('api_comparison_not_confirmed')
            receipt.update(result)
            receipt['inventory_state'] = self.journal.require('rebuild')['inventory_state']
            receipt['full_platform_restore_certified'] = False
            receipt['scope'] = 'authorized_fixture_recovered_with_explicit_inventory_omissions'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=sorted(STAGES))
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--external-writers-stopped', action='store_true')
    args = parser.parse_args()
    try:
        controller = Controller(args.plan)
        if args.phase == 'quiesced':
            controller.quiesced(args.external_writers_stopped)
        else:
            getattr(controller, args.phase)()
        result = controller.journal.require(args.phase)
        print(json.dumps({k: result[k] for k in ('stage', 'state', 'database_count', 'inventory_state', 'positive_complete', 'negative_persisted') if k in result}))
        return 0
    except BaseException:
        # CLI intentionally suppresses traceback and external output; receipts retain safe phase state.
        print(json.dumps({'state':'failed_or_uncertain','error':'recovery_phase_not_confirmed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())