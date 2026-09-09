from urllib.parse import quote

from fastapi import HTTPException
import httpx
from pydantic import BaseModel, Field, ValidationError

from .config import Settings
from .security import ServiceSecurity


class Principal(BaseModel):
    id: str
    subjects: list[str]
    permissions: list[str] = Field(default_factory=list)
    auth_epoch: int


class Decision(BaseModel):
    allowed: bool
    auth_epoch: int
    acl_domain: str = ''
    acl_version: int = 0


class HTTPAuthorizer:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.security = ServiceSecurity.from_settings(settings)
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=False)

    async def close(self):
        if self._owned_client:
            await self.client.aclose()

    async def request(self, method: str, path: str, target: str,
                      token: str | None = None, json=None) -> httpx.Response:
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Internal API paths must be relative to a configured service')
        headers = {'X-Service-Token': self.security.issue(target)}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        try:
            response = await self.client.request(method, self.settings.url_for(target) + path,
                                                 headers=headers, json=json, follow_redirects=False)
        except httpx.HTTPError:
            raise HTTPException(503, 'Required internal service unavailable') from None
        if response.status_code in {401, 403}:
            raise HTTPException(response.status_code, 'Authentication or authorization denied')
        if not 200 <= response.status_code < 300:
            raise HTTPException(503, 'Required internal service unavailable')
        return response

    @staticmethod
    def _json(response):
        try:
            return response.json()
        except ValueError:
            raise HTTPException(503, 'Invalid authorization response') from None

    async def resolve(self, token: str) -> Principal:
        response = await self.request('POST', '/internal/v1/resolve', 'auth', token, {})
        try:
            return Principal.model_validate(self._json(response)['principal'])
        except (KeyError, TypeError, ValidationError):
            raise HTTPException(503, 'Invalid authorization response') from None

    async def authorize(self, token: str, action: str, space_id: str,
                        resource_id: str | None = None) -> Decision:
        body = {'action': action, 'space_id': space_id}
        if resource_id is not None:
            body['resource_id'] = resource_id
        response = await self.request('POST', '/internal/v1/authorize', 'auth', token, body)
        try:
            return Decision.model_validate(self._json(response))
        except ValidationError:
            raise HTTPException(503, 'Invalid authorization response') from None

    async def require(self, token: str, action: str, space_id: str,
                      resource_id: str | None = None) -> Decision:
        decision = await self.authorize(token, action, space_id, resource_id)
        if not decision.allowed:
            raise HTTPException(403, 'Resource access denied')
        return decision

    async def policy(self, space_id: str, resource_id: str) -> dict:
        path = f'/internal/v1/policies/{quote(space_id,safe="")}/{quote(resource_id,safe="")}'
        response = await self.request('GET', path, 'iam')
        value = self._json(response)
        if not isinstance(value, dict):
            raise HTTPException(503, 'Invalid policy response')
        return value