"""Workload HTTP contracts; graph never borrows another service's database or model key."""
from urllib.parse import quote

import httpx

from .projection import digest
from .schemas import unavailable


def safe_json(response):
    try:
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise unavailable('invalid_dependency_response', 'Internal graph dependency response invalid') from None


class Clients:
    def __init__(self, settings, authorizer):
        self.settings, self.auth = settings, authorizer

    async def call(self, method, path, target, token=None, body=None):
        return safe_json(await self.auth.request(method, path, target, token, body))

    async def projection(self, page_id, revision_id):
        return await self.call('GET', f'/internal/v1/projections/pages/{quote(page_id, safe="")}/revisions/{quote(revision_id, safe="")}', 'knowledge')

    async def pages(self, token, records, request, guard, *, inspection=False):
        keys = sorted({(r['graph'].page_id, r['graph'].revision_id) for r in records})
        permitted = set()
        for start in range(0, len(keys), 100):
            batch = keys[start:start+100]
            data = await self.call('POST', '/internal/v1/pages/authorize', 'knowledge', token,
                {'pages':[{'page_id':p, 'revision_id':r} for p,r in batch], 'include_historical':request.include_historical,
                 'as_of':request.as_of.isoformat() if request.as_of else None})
            try:
                decisions = data['decisions']
                if len(decisions) != len(batch) or {(d['page_id'], d['revision_id']) for d in decisions} != set(batch):
                    raise ValueError
                for decision in decisions:
                    if type(decision['allowed']) is not bool:
                        raise ValueError
                    if decision.get('authorized' if inspection else 'allowed') is True and decision['space_id'] in request.space_ids:
                        permitted.add((decision['page_id'], decision['revision_id']))
            except (KeyError, TypeError, ValueError):
                raise unavailable('invalid_authorization_response', 'Canonical graph authorization response invalid') from None
            await guard.finish()
        return {r['projection_id'] for r in records if (r['graph'].page_id, r['graph'].revision_id) in permitted}

    async def evidence(self, token, refs, guard):
        permitted = set()
        for start in range(0, len(refs), 100):
            batch = refs[start:start+100]
            data = await self.call('POST', '/internal/v1/evidence/authorize', 'knowledge', token, {'evidence':batch})
            try:
                decisions = data['decisions']
                if len(decisions) != len(batch):
                    raise ValueError
                for original, decision in zip(batch, decisions, strict=True):
                    if decision['evidence'] != original or type(decision['allowed']) is not bool:
                        raise ValueError
                    if decision['allowed']:
                        permitted.add(digest(original))
            except (KeyError, TypeError, ValueError):
                raise unavailable('invalid_authorization_response', 'Canonical graph evidence response invalid') from None
            await guard.finish()
        return permitted


class MachineTokens:
    def __init__(self, settings, client=None):
        self.settings, self.owned = settings, client is None
        self.client = client or httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False)

    async def close(self):
        if self.owned:
            await self.client.aclose()

    async def token(self):
        s = self.settings
        if not all((s.graphiti_oidc_token_url,s.graphiti_oidc_client_id,s.graphiti_oidc_client_secret)):
            raise unavailable('worker_authorization_required', 'Configure and grant the graph service account')
        try:
            response = await self.client.post(s.graphiti_oidc_token_url, data={'grant_type':'client_credentials',
                'client_id':s.graphiti_oidc_client_id, 'client_secret':s.graphiti_oidc_client_secret, 'scope':'knowledge:read'}, follow_redirects=False)
            if response.status_code != 200:
                raise unavailable('worker_authorization_required', 'Graph service account authentication failed')
            value = response.json()
            token = value['access_token']
            if not isinstance(token,str) or not token or value['token_type'].lower() != 'bearer':
                raise ValueError
            return token
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise unavailable('worker_authorization_required', 'Graph service account identity unavailable') from None