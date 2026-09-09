"""Keep each composite decision at one IAM epoch, including nested collection reads."""

from dataclasses import dataclass
from functools import wraps

from .schemas import KnowledgeError


@dataclass
class AuthorizationEpoch:
    value: int | None = None

    def observe(self, epoch: int | None) -> None:
        if type(epoch) is not int or epoch < 0:
            raise KnowledgeError(503, 'authorization_unavailable', 'Authorization epoch is missing or invalid')
        if self.value is not None and self.value != epoch:
            raise KnowledgeError(503, 'authorization_changed', 'Authorization changed during the operation; retry the request')
        self.value = epoch


def coherent_authorization(method):
    @wraps(method)
    async def operation(self, *args, **kwargs):
        # A nested page/history/evidence read contributes to its outer collection's epoch.
        if self._authorization.get() is not None:
            return await method(self, *args, **kwargs)
        context_token = self._authorization.set(AuthorizationEpoch())
        try:
            return await method(self, *args, **kwargs)
        finally:
            self._authorization.reset(context_token)

    return operation