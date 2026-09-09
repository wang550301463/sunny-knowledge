from copy import deepcopy
from importlib import import_module

import pytest

from knowledge_platform.common.auth import Principal

from .test_authorization import Auth
from .test_projection import projection


def constraint(**changes):
    return {
        "context_id": "ctx", "channel_id": "bot", "conversation_key": "private-isolation",
        "agent_id": "agent", "agent_configuration_id": "config", "space_ids": ["engineering"],
        "chat_type": "group", "audience_id": "group-1", "group_key": "registered-group",
        "message_id": "message", **changes,
    }


class ChannelAuth(Auth):
    def __init__(self):
        super().__init__()
        self.context = constraint()

    async def resolve(self, token):
        return Principal(id="alice", subjects=self.subjects, auth_epoch=self.epoch,
                         channel_context=self.context)


@pytest.mark.parametrize("service", ["retrieval", "graphiti"])
async def test_channel_prefilter_is_actor_and_audience_for_every_policy(service):
    adapter = import_module(f"knowledge_platform.{service}.authorization")
    auth = ChannelAuth()
    cache = adapter.PolicyCache()
    for doc_subjects, space_subjects, allowed in [
        (["user:alice"], ["group:engineering", "audience:group-1"], False),
        (["user:alice", "audience:other"], ["group:engineering", "audience:group-1"], False),
        (["audience:group-1"], ["group:engineering", "audience:group-1"], False),
        (["user:alice", "audience:group-1"], ["group:engineering"], False),
        (["user:alice", "audience:group-1"], ["group:engineering", "audience:group-1"], True),
    ]:
        value = projection()
        for policy in value["policies"]:
            policy["space_read_subjects"] = space_subjects
            policy["auth_epoch"] = auth.epoch
        value["policies"][0]["resource_read_subjects"] = doc_subjects
        auth.current = deepcopy(value["policies"])
        guard = await adapter.AuthorizationGuard.begin(auth, "channel-token", ["engineering"])
        assert bool(await cache.allowed(guard, [value])) is allowed
        auth.epoch += 1


@pytest.mark.parametrize("service", ["retrieval", "graphiti"])
async def test_channel_private_scope_and_identity_changes_invalidate_projection_access(service):
    adapter = import_module(f"knowledge_platform.{service}.authorization")
    auth = ChannelAuth()
    auth.context = constraint(chat_type="single", audience_id="", group_key="", space_ids=["other"])
    guard = await adapter.AuthorizationGuard.begin(auth, "channel-token", [])
    assert await adapter.PolicyCache().allowed(guard, [projection()]) == []
    auth.context["conversation_key"] = "cleared-generation"
    with pytest.raises(Exception, match="Authorization changed"):
        await guard.finish()
