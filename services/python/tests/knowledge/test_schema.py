import pytest
from pydantic import ValidationError


def evidence(**updates):
    from knowledge_platform.knowledge.schemas import EvidenceRef
    data = dict(resource_id='source:payments', revision_id='11111111-1111-4111-8111-111111111111', source_id='git:payments', source_revision='a' * 40, path='main.go', start_line=1, end_line=2, kind='code')
    return EvidenceRef(**(data | updates))


def test_evidence_rejects_unfixed_locations_and_revision():
    for updates in ({'revision_id': ''}, {'source_revision': ''}, {'path': '../secret'}, {'start_line': 0}, {'end_line': 0}, {'start_line': 4, 'end_line': 2}):
        with pytest.raises(ValidationError):
            evidence(**updates)


def test_evidence_rejects_reversed_validity():
    with pytest.raises(ValidationError):
        evidence(valid_from='2026-03-01T00:00:00Z', valid_until='2026-02-01T00:00:00Z')


def test_claim_cannot_turn_digest_into_independent_evidence():
    with pytest.raises(ValidationError):
        evidence(kind='digest')


def test_content_requires_supported_formal_claims():
    from knowledge_platform.knowledge.schemas import PageContent
    with pytest.raises(ValidationError):
        PageContent(title='Formal', markdown='Fact', claims=[{'id': 'claim:1', 'text': 'Fact', 'kind': 'fact', 'evidence': []}])


def test_inference_and_gaps_are_explicitly_distinct():
    from knowledge_platform.knowledge.schemas import PageContent
    content = PageContent(title='Known gap', markdown='Unknown.', claims=[{'id': 'claim:gap', 'text': 'No deployment evidence', 'kind': 'gap', 'evidence': []}])
    assert content.claims[0].kind == 'gap'