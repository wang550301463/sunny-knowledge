"""Generated operations are transport inputs, never alternate identity boundaries."""
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def generator():
    path = ROOT / 'knowledge-docker/scripts/contract_generate.py'
    spec = importlib.util.spec_from_file_location('contract_generate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_clients_and_specs_have_reproducible_drift_check(tmp_path):
    module = generator()
    artifacts = module.generate(ROOT)
    assert 'contracts/generated/openapi/agent.json' in artifacts
    assert any(path.endswith('/types.ts') for path in artifacts)
    assert any(path.endswith('/operations.go') for path in artifacts)
    assert any(path.endswith('/types.py') for path in artifacts)
    module.write_outputs(tmp_path, artifacts)
    assert module.check_outputs(tmp_path, artifacts) == []
    path = tmp_path / 'contracts/generated/openapi/agent.json'
    path.write_text('{}')
    assert 'changed: contracts/generated/openapi/agent.json' in module.check_outputs(tmp_path, artifacts)


def test_generated_operation_prepares_only_declared_safe_relative_input():
    client = importlib.import_module('knowledge_platform.generated_contracts.client')
    ops = importlib.import_module('knowledge_platform.generated_contracts.operations')
    op = ops.OPERATIONS['knowledge_get_api_v1_pages_by_page_id']
    prepared = client.prepare(op, path={'page_id': 'a/b?x=1%'}, query={})
    assert prepared.path == '/api/v1/pages/a%2Fb%3Fx%3D1%25'
    assert prepared.service == 'knowledge'
    assert prepared.method == 'GET'
    assert not hasattr(prepared, 'token')
    with pytest.raises(ValueError):
        client.prepare(op, path={'page_id': '..'})
    with pytest.raises(ValueError):
        client.prepare(op, path={'page_id': 'ok'}, query={'principal': 'admin'})
    with pytest.raises(ValueError):
        client.prepare(op, path={'page_id': 'ok'}, headers={'Authorization': 'forged'})


def test_generated_schema_keeps_source_and_wiki_revisions_distinct():
    doc = json.loads((ROOT / 'contracts/generated/openapi/agent.json').read_text())
    schema = doc['components']['schemas']['Citation']
    assert 'revision_id' in schema['properties']
    assert schema['properties']['evidence']['$ref'].endswith('/EvidenceRef')
    assert doc['components']['schemas']['EvidenceRef']['properties']['revision_id']['type'] == 'string'
    coverage = json.loads((ROOT / 'contracts/generated/coverage.json').read_text())
    assert coverage['untyped_responses']
    assert coverage['version'] == '1.0.0'