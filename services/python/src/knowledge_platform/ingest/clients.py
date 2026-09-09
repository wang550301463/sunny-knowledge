"""Live delegated HTTP boundary. No bearer or credentials persist outside this process."""
import httpx
from fastapi import HTTPException
from knowledge_platform.common.observability import trace_headers
from knowledge_platform.common.security import ServiceSecurity
from .schemas import IngestError


class MachineTokens:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        self.owned = client is None

    async def token(self):
        if not all((self.settings.ingest_oidc_token_url, self.settings.ingest_oidc_client_id,
                    self.settings.ingest_oidc_client_secret)):
            raise IngestError(403, 'worker_authorization_required', 'Configure and grant the ingest service account')
        try:
            response = await self.client.post(self.settings.ingest_oidc_token_url,
                data={'grant_type': 'client_credentials', 'client_id': self.settings.ingest_oidc_client_id,
                      'client_secret': self.settings.ingest_oidc_client_secret, 'scope': 'knowledge:read knowledge:write'},
                follow_redirects=False)
            if response.status_code != 200:
                raise IngestError(403, 'worker_authorization_required', 'Ingest service account authentication failed')
            obj = response.json()
            token = obj.get('access_token')
            if not isinstance(token, str) or not token or obj.get('token_type', '').lower() != 'bearer':
                raise ValueError
            return token
        except (httpx.HTTPError, ValueError):
            raise IngestError(503, 'dependency_unavailable', 'Service account identity provider unavailable') from None

    async def close(self):
        if self.owned: await self.client.aclose()


class InternalClient:
    def __init__(self, settings, security=None, client=None):
        self.settings = settings
        self.security = security or ServiceSecurity.from_settings(settings)
        self.client = client or httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=False)
        self.owned = client is None

    async def request(self, method, path, token, body=None, target='knowledge', allow_missing=False):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Expected configured internal path')
        try:
            response = await self.client.request(method, self.settings.url_for(target) + path,
                headers={**trace_headers(), 'X-Service-Token': self.security.issue(target),
                         'Authorization': 'Bearer ' + token}, json=body, follow_redirects=False)
        except httpx.HTTPError:
            raise IngestError(503, 'dependency_unavailable', 'Required internal service unavailable') from None
        if response.status_code == 404 and allow_missing:
            return None
        if not 200 <= response.status_code < 300:
            # Only trusted bounded codes are retained, never upstream error text or response bodies.
            code = {401: 'unauthenticated', 403: 'forbidden', 409: 'publication_conflict',
                    422: 'invalid_publication'}.get(response.status_code, 'dependency_unavailable')
            if response.status_code == 409:
                try:
                    if response.json().get('error', {}).get('code') == 'review_required':
                        code = 'review_required'
                except ValueError:
                    pass
            raise IngestError(response.status_code if response.status_code in {401, 403, 409, 422} else 503,
                              code, 'Required internal operation was not accepted')
        try:
            value = response.json()
            if not isinstance(value, dict): raise ValueError
            return value
        except ValueError:
            raise IngestError(503, 'dependency_unavailable', 'Invalid internal response') from None

    async def register_resource(self, token, space_id, resource_id):
        return await self.request('POST', '/internal/v1/source-resources', token,
                                  {'space_id': space_id, 'resource_id': resource_id}, target='iam')

    async def close(self):
        if self.owned: await self.client.aclose()