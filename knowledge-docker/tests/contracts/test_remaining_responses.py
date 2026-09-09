"""All remaining shapes, especially authorization-redacted branches, remain explicit."""

import pytest

from test_export import ROOT, exporter
from test_responses import serialized


@pytest.mark.parametrize("domain", ["knowledge", "agent", "mcp"])
def test_remaining_python_routes_have_native_data_response_models(domain):
    import importlib
    from fastapi.routing import APIRoute

    with exporter().isolated_environment():
        app = importlib.import_module(f"knowledge_platform.{domain}.app").create_app()
    assert all(route.response_model is not None for route in app.routes if isinstance(route, APIRoute) and route.path not in {"/healthz", "/readyz"} and not route.path.endswith('/export'))


@pytest.mark.parametrize('domain,path', [('auth','/internal/v1/channel-token'),('auth','/internal/v1/channel-run-token'),('iam','/api/v1/spaces/{id}'),('channel','/api/v1/channel-bindings/claim')])
def test_remaining_go_outputs_have_native_response_schema(domain, path):
    value = exporter().export_go(ROOT)
    doc = value['services'][domain] if 'services' in value else value[domain]
    operation = doc['paths'][path]['get' if domain == 'iam' else 'post']
    assert operation['x-response-typing'] == 'typed'


@pytest.mark.asyncio
@pytest.mark.parametrize('hidden', [False, True])
async def test_summary_hidden_branch_does_not_gain_content_runs_or_null_placeholders(hidden):
    from knowledge_platform.agent.responses import SummaryView

    raw = {'id':'summary', 'version':1, 'created_at':'2026-09-09T00:00:00+00:00', 'independent_evidence_count':0, 'content_hidden':hidden}
    if not hidden:
        raw.update(run_ids=['run'], content=[{'run_id':'run','answer':{'facts':[],'inferences':[],'gaps':['unknown']},'answer_complete':True,'citations':[]}])
    assert await serialized(SummaryView, raw) == raw


@pytest.mark.asyncio
async def test_page_and_evidence_denials_remain_only_the_native_false_identity_shape():
    from knowledge_platform.knowledge.responses import EvidenceDecisions, PageDecisions

    denied = {'page_id':'p','revision_id':'r','allowed':False}
    assert await serialized(PageDecisions, {'decisions':[denied]}) == {'decisions':[denied]}
    ref = {'resource_id':'res','revision_id':'snapshot','source_id':'source','source_revision':'commit','path':'main.go','kind':'code','start_line':1,'end_line':1,'valid_from':None,'valid_until':None}
    raw = {'decisions':[{'evidence':ref,'allowed':False}]}
    assert await serialized(EvidenceDecisions, raw) == raw


@pytest.mark.asyncio
async def test_agent_run_citation_snapshot_is_exactly_nine_selected_public_fields():
    from knowledge_platform.agent.responses import CitationSnapshot

    raw = {key:'value' for key in ('id','source_id','source_revision','resource_id','space_id','path','kind','text','sha256')}
    raw['kind']='code'
    assert set(CitationSnapshot.model_fields) == set(raw)
    assert await serialized(CitationSnapshot, raw) == raw