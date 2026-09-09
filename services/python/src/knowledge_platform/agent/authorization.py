"""Live OAuth scope, owner, space and original-provenance checks at one IAM epoch."""
from dataclasses import dataclass

from pydantic import ValidationError

from knowledge_platform.common.auth import Principal

from .schemas import fail


@dataclass
class Guard:
    auth: object
    token: str
    principal: Principal
    scopes: set[str]

    @classmethod
    async def begin(cls, auth, token, scope='knowledge:read', owner=None):
        response = await auth.request('POST', '/internal/v1/resolve', 'auth', token, {})
        try:
            data = response.json()
            principal = Principal.model_validate(data['principal'])
            scopes = data['scopes']
            if not isinstance(scopes, list) or any(not isinstance(s, str) for s in scopes):
                raise ValueError
        except (ValueError, KeyError, TypeError, ValidationError):
            raise fail('invalid_authorization') from None
        if scope not in scopes:
            raise fail('scope_required', 'Required OAuth scope is missing', 403)
        if owner is not None and principal.id != owner:
            raise fail('not_found', 'Agent resource not found', 404)
        return cls(auth, token, principal, set(scopes))

    def epoch(self, epoch):
        if type(epoch) is not int or epoch != self.principal.auth_epoch:
            raise fail('authorization_changed', 'Authorization changed during the operation')

    async def allowed(self, action, space, resource=None):
        decision = await self.auth.authorize(self.token, action, space, resource)
        self.epoch(decision.auth_epoch)
        return decision.allowed

    async def require(self, action, space, resource=None):
        if not await self.allowed(action, space, resource):
            raise fail('forbidden', 'Current resource authorization denied', 403)

    async def finish(self):
        current = await self.auth.resolve(self.token)
        self.epoch(current.auth_epoch)
        if current.id != self.principal.id or set(current.subjects) != set(self.principal.subjects):
            raise fail('authorization_changed')

    async def dependencies(self, clients, dependencies, *, display=False):
        # Page authorization includes immutable inherited provenance, even after citations are removed.
        for dependency in dependencies:
            if not await self.allowed('read', dependency['space_id'], dependency['page_id']):
                if display:
                    await self.finish()
                    return False
                raise fail('evidence_unavailable', 'Original evidence is no longer authorized', 403)
            body = {'pages':[{'page_id':dependency['page_id'], 'revision_id':dependency['revision_id']}], 'include_historical':display or dependency.get('mode') == 'historical', 'as_of':dependency.get('as_of')}
            data = await clients.call('knowledge', 'POST', '/internal/v1/pages/authorize', self.token, body)
            decisions = data.get('decisions')
            if not isinstance(decisions, list) or len(decisions) != 1:
                raise fail('invalid_authorization')
            decision = decisions[0]
            if decision.get('page_id') != dependency['page_id'] or decision.get('revision_id') != dependency['revision_id']:
                raise fail('invalid_authorization')
            flag = 'authorized' if display or dependency.get('inspection') else 'allowed'
            if decision.get(flag) is not True:
                if display:
                    await self.finish()
                    return False
                raise fail('evidence_unavailable', 'Original evidence is no longer eligible', 403)
            if decision.get('space_id') != dependency['space_id']:
                raise fail('invalid_authorization')
            evidence = dependency.get('evidence', [])
            for start in range(0, len(evidence), 100):
                refs = evidence[start:start+100]
                data = await clients.call('knowledge', 'POST', '/internal/v1/evidence/authorize', self.token, {'evidence':refs})
                decisions = data.get('decisions')
                if not isinstance(decisions, list) or len(decisions) != len(refs):
                    raise fail('invalid_authorization')
                for ref, decision in zip(refs, decisions, strict=True):
                    if decision.get('evidence') != ref:
                        raise fail('invalid_authorization')
                    if decision.get('allowed') is not True:
                        if display:
                            await self.finish()
                            return False
                        raise fail('evidence_unavailable', 'Original evidence is no longer authorized', 403)
        await self.finish()
        return True