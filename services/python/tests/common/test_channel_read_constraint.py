import pytest
from pydantic import ValidationError

from knowledge_platform.common.auth import ChannelConstraint


def context():
    return {
        "context_id": "ctx",
        "channel_id": "bot",
        "conversation_key": "conversation",
        "agent_id": "agent",
        "agent_configuration_id": "version",
        "space_ids": ["space"],
        "chat_type": "group",
        "audience_id": "audience",
        "group_key": "group",
        "message_id": "message",
    }


def test_read_run_constraint_preserves_separate_group_authority():
    value = ChannelConstraint.model_validate({**context(), "read_run_id": "run"})
    assert value.read_run_id == "run"
    assert value.audience_id == "audience"
    assert value.space_ids == ["space"]
    assert ChannelConstraint.model_validate(context()).read_run_id is None


@pytest.mark.parametrize("run", ["", "../run", "x" * 257, 1, True, ["run"]])
def test_read_run_constraint_rejects_malformed_ids(run):
    with pytest.raises(ValidationError):
        ChannelConstraint.model_validate({**context(), "read_run_id": run})
