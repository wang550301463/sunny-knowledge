import hashlib
import json

import pytest


def projection(**changes):
    ref = {"resource_id":"source:pay","revision_id":"snapshot-a","source_id":"git:pay","source_revision":"a"*40,"path":"pay.go","start_line":1,"end_line":2,"kind":"code","valid_from":None,"valid_until":None}
    clauses = [["group:engineering"], ["user:alice", "user:bob"]]
    value = {"page_id":"page:pay","space_id":"engineering","revision_id":"revision-a","version":1,"current_revision":"revision-a","current_version":1,"is_current":True,"created_at":"2026-01-01T00:00:00Z","content":{"title":"Payment","markdown":"# Payment\nPay calls Charge.","entity_type":"Module","claims":[{"id":"claim:call","text":"Pay calls Charge.","kind":"fact","entity_type":"File","evidence":[ref],"state":"valid","valid_from":None,"valid_until":None}],"evidence":[ref],"state":"valid","valid_from":None,"valid_until":None},"read_clauses":clauses,"acl_domain":hashlib.sha256(json.dumps({"space_id":"engineering","read_clauses":clauses},sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest(),"policies":[{"space_id":"engineering","resource_id":"page:pay","space_read_subjects":["group:engineering"],"resource_read_subjects":["user:alice","user:bob"],"acl_version":7,"auth_epoch":9},{"space_id":"engineering","resource_id":"source:pay","space_read_subjects":["group:engineering"],"resource_read_subjects":None,"acl_version":4,"auth_epoch":9}],"source_snapshots":[{"id":"snapshot-a","space_id":"engineering","resource_id":"source:pay","source_id":"git:pay","source_revision":"a"*40,"path":"pay.go","kind":"code","sha256":"d"*64}]}
    value.update(changes)
    return value


def test_fragment_compilation_preserves_full_acl_and_exact_source_pins():
    from knowledge_platform.retrieval.projection import compile_fragments
    value = projection()
    fragments = compile_fragments(value, configuration_id="model-v1", dimensions=3)
    assert len(fragments) == 2
    fact = next(f for f in fragments if f.kind == "fact")
    assert fact.text == "Pay calls Charge."
    assert fact.evidence[0].source_revision == "a" * 40
    assert fact.acl_domain == value["acl_domain"]
    assert fact.read_clauses == value["read_clauses"]
    assert fact.acl_epoch == 9
    assert fact.acl_version == 7
    assert fact.revision_id == "revision-a"
    assert fact.entity_ids == ["claim:call", "page:pay"]
    assert fact.id == compile_fragments(value, configuration_id="model-v1", dimensions=3)[1].id


def test_invalid_claim_time_is_hard_constraint_and_inference_keeps_kind():
    from knowledge_platform.retrieval.projection import compile_fragments
    value = projection()
    value["content"]["claims"][0].update(kind="inference", valid_until="2026-02-01T00:00:00Z")
    fragments = compile_fragments(value, configuration_id="model-v1", dimensions=3)
    assert {f.valid_until.isoformat() for f in fragments} == {"2026-02-01T00:00:00+00:00"}
    assert fragments[1].kind == "inference"
    value["content"]["claims"][0]["state"] = "stale"
    assert all(f.state == "stale" for f in compile_fragments(value, configuration_id="model-v1", dimensions=3))


def test_projection_rejects_incomplete_or_forged_policy_conjunction():
    from knowledge_platform.retrieval.projection import compile_fragments
    from knowledge_platform.retrieval.schemas import RetrievalError
    for mutation in (lambda p:p.update(read_clauses=[["group:engineering"]]), lambda p:p.update(acl_domain="fake"), lambda p:p["policies"][1].update(auth_epoch=10),lambda p:p.update(policies=p["policies"][:1])):
        value = projection()
        mutation(value)
        with pytest.raises(RetrievalError):
            compile_fragments(value, configuration_id="model-v1", dimensions=3)


def test_chunk_boundaries_do_not_create_independent_summary_evidence():
    from knowledge_platform.retrieval.projection import compile_fragments
    value = projection()
    value["content"]["markdown"] = "甲" * 9000
    fragments = compile_fragments(value, configuration_id="model-v1", dimensions=3, chunk_chars=2000)
    narratives = [f for f in fragments if f.kind == "narrative"]
    assert "".join(f.text for f in narratives) == value["content"]["markdown"]
    assert len({json.dumps(f.evidence[0].model_dump(mode="json"),sort_keys=True) for f in narratives}) == 1
    assert all(len(f.text) <= 2000 for f in narratives)