import math

import pytest


def test_equal_weight_hybrid_rrf_and_stable_ties():
    from knowledge_platform.retrieval.ranking import fuse

    result = fuse({"bm25": ["exact", "shared"], "vector": ["semantic", "shared"]})
    assert [item.id for item in result] == ["shared", "exact", "semantic"]
    assert result[0].score == pytest.approx(2 / 62)
    assert result[0].contributions == {"bm25": 1 / 62, "vector": 1 / 62}


def test_graph_weight_and_duplicate_fragments_do_not_multiply_support():
    from knowledge_platform.retrieval.ranking import fuse

    result = fuse(
        {"bm25": ["a", "a", "b"], "graph": ["a", "graph-only", "graph-only"]},
        weights={"graph": 0.5},
    )
    by_id = {item.id: item for item in result}
    assert by_id["a"].score == pytest.approx(1.5 / 61)
    assert by_id["b"].score == pytest.approx(1 / 62)
    assert by_id["graph-only"].score == pytest.approx(0.5 / 62)


def test_input_and_output_budgets_are_explicit():
    from knowledge_platform.retrieval.ranking import fuse

    assert fuse({}, limit=30) == []
    assert len(fuse({"bm25": [str(i) for i in range(50)]}, limit=30)) == 30
    for value in [0, -1, math.inf, math.nan]:
        with pytest.raises(ValueError):
            fuse({"vector": ["a"]}, weights={"vector": value})
    with pytest.raises(ValueError):
        fuse({"vector": ["a"]}, k=0)


def test_final_fusion_preserves_original_retrieval_ranks_with_half_weight_graph():
    from knowledge_platform.retrieval.ranking import fuse

    bm25 = ["a", "b", "c"]
    vector = ["c", "b", "a"]
    initial = fuse({"bm25": bm25, "vector": vector}, limit=30)
    final = fuse({"bm25": bm25, "vector": vector, "graph": ["b", "d"]}, weights={"graph": 0.5}, limit=60)
    assert final[0].id == "b"
    before = next(item.score for item in initial if item.id == "b")
    assert final[0].score == pytest.approx(before + 0.5 / 61)
