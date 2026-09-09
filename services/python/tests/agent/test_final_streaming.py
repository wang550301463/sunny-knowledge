import json

import pytest

from knowledge_platform.agent.answer_stream import AnswerDecoder
from knowledge_platform.agent.schemas import AgentError


def line(kind, text, citations=()):
    return json.dumps(
        {"kind": kind, "text": text, "citation_ids": list(citations)}, ensure_ascii=False
    ) + "\n"


def test_decoder_emits_only_complete_validated_lines_and_never_partial_json():
    decoder = AnswerDecoder({"real"})
    payload = line("fact", "支付调用账本🧭", ["real"])
    assert list(decoder.feed(payload[:-1])) == []
    assert list(decoder.feed("\n")) == [
        {"index": 0, "kind": "fact", "text": "支付调用账本🧭", "citation_ids": ["real"]}
    ]
    assert decoder.answer["facts"][0]["text"] == "支付调用账本🧭"
    assert list(decoder.feed('{"done":true}\n')) == []
    decoder.finish()


@pytest.mark.parametrize(
    "suffix,code",
    [
        (line("fact", "invented", ["fake"]), "unsupported_citation"),
        (line("gap", "misleading", ["real"]), "invalid_answer_block"),
        ('{"kind":"gap","text":"a","text":"b","citation_ids":[]}\n', "invalid_answer_block"),
        ('{"reasoning":"secret"}\n', "invalid_answer_block"),
        ('{"done":true}\n' + line("gap", "after done"), "invalid_answer_block"),
    ],
)
def test_decoder_preserves_prefix_but_rejects_invalid_later_records(suffix, code):
    decoder = AnswerDecoder({"real"})
    list(decoder.feed(line("fact", "validated", ["real"])))
    with pytest.raises(AgentError) as error:
        list(decoder.feed(suffix))
    assert error.value.code == code
    assert decoder.answer["facts"] == [{"text": "validated", "citation_ids": ["real"]}]


@pytest.mark.parametrize("ending", ["", '{"done":true}', '{"kind":"gap"'])
def test_missing_terminal_record_or_truncated_line_is_incomplete(ending):
    decoder = AnswerDecoder(set())
    list(decoder.feed(line("gap", "not enough evidence") + ending))
    with pytest.raises(AgentError, match="incomplete_answer_stream"):
        decoder.finish()


def test_block_count_limit_is_checked_before_emitting_overflow():
    decoder = AnswerDecoder(set())
    assert len(list(decoder.feed(line("gap", "missing") * 20))) == 20
    with pytest.raises(AgentError):
        list(decoder.feed(line("gap", "overflow")))
    assert len(decoder.answer["gaps"]) == 20