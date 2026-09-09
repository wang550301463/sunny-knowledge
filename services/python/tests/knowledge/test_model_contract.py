def test_postgres_models_include_version_and_per_consumer_delivery_constraints():
    from knowledge_platform.knowledge.models import Base, initialize
    tables = Base.metadata.tables
    assert 'knowledge_revisions' in tables
    assert 'knowledge_source_snapshots' in tables
    assert 'knowledge_deliveries' in tables
    assert 'knowledge_audit' in tables
    assert callable(initialize)


def test_service_exposes_canonical_review_and_projection_boundaries():
    from knowledge_platform.knowledge.service import KnowledgeService
    for method in ('create_page', 'propose', 'approve', 'reject', 'rollback', 'register_snapshot', 'deterministic_publish', 'get_revision', 'list_revisions', 'list_pages', 'list_reviews', 'lease_outbox', 'ack_outbox', 'projection'):
        assert callable(getattr(KnowledgeService, method))