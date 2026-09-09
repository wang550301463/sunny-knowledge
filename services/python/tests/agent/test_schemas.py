import json

import pytest
from pydantic import ValidationError


def test_presets_are_explicit_and_no_model_is_guessed():
    from knowledge_platform.agent.schemas import PRESETS, AgentConfig

    assert set(PRESETS) == {'knowledge_qa', 'dependency_impact', 'incident_history', 'maintenance'}
    for mode, preset in PRESETS.items():
        config = AgentConfig(mode=mode, space_ids=['engineering'])
        assert config.model_configuration_id is None
        assert config.budget.model_rounds == 8
        assert config.budget.tool_calls == 20
        assert config.budget.parallel_reads == 2
        assert config.budget.seconds == 180
        assert preset['tools']


@pytest.mark.parametrize('kwargs', [
    {'tools': ['shell']}, {'space_ids': ['s', 's']}, {'tools': ['search', 'search']},
    {'budget': {'seconds': 181}}, {'budget': {'model_rounds': 9}},
    {'budget': {'tool_calls': 21}}, {'budget': {'parallel_reads': 3}},
    {'tool_space_ids': {'search': ['secret']}}, {'principal': 'admin'},
])
def test_configuration_is_bounded_and_cannot_expand_scope(kwargs):
    from knowledge_platform.agent.schemas import AgentConfig

    with pytest.raises(ValidationError):
        AgentConfig.model_validate({'space_ids': ['s'], **kwargs})


def test_final_answer_rejects_invented_citations_and_unknown_fields():
    from knowledge_platform.agent.schemas import AgentError, parse_answer

    answer = {'facts': [{'text': 'An exact supported statement', 'citation_ids': ['c1']}],
              'inferences': [], 'gaps': []}
    assert parse_answer(json.dumps(answer), {'c1'}) == answer
    with pytest.raises(AgentError, match='citation'):
        parse_answer(json.dumps(answer), set())
    answer['reasoning'] = 'private internal chain'
    with pytest.raises(AgentError):
        parse_answer(json.dumps(answer), {'c1'})


def test_all_tools_are_strict_and_write_tools_cannot_hide_in_read_parallel_group():
    from knowledge_platform.agent.schemas import AgentError, READ_TOOLS, validate_call, tool_schema

    assert READ_TOOLS == {'search', 'get', 'traverse', 'timeline'}
    assert tool_schema('search')['function']['parameters']['additionalProperties'] is False
    with pytest.raises(AgentError):
        validate_call({'id': 'x', 'type': 'function', 'function': {
            'name': 'search', 'arguments': '{"query":"q","space_ids":["s"],"principal":"a"}'}})
    with pytest.raises(AgentError):
        validate_call({'id': 'x', 'type': 'function', 'function': {
            'name': 'shell', 'arguments': '{}'}})
    with pytest.raises(AgentError):
        validate_call({'id': 'x', 'type': 'function', 'function': {
            'name': 'search', 'arguments': '{"query":"a","query":"b","space_ids":["s"]}'}})