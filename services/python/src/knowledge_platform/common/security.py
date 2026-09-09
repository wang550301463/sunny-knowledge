"""Short-lived workload identities. End-user authorization is a separate live check."""
from dataclasses import dataclass, field
import json
from pathlib import Path
import time

from fastapi import HTTPException, Request
import jwt

ISSUER = 'knowledge-services'
MAX_LIFETIME = 60


@dataclass
class ServiceSecurity:
    name: str
    private_key: str = field(repr=False)
    public_keys: dict[str, str] = field(repr=False)

    @classmethod
    def from_settings(cls, settings):
        public = json.loads(Path(settings.service_public_keys_file).read_text())
        if settings.service_name not in public:
            raise ValueError('Service missing from workload public-key registry')
        return cls(settings.service_name, Path(settings.service_private_key_file).read_text(), public)

    def issue(self, audience: str) -> str:
        if audience not in self.public_keys:
            raise ValueError('Unknown internal service audience')
        now = int(time.time())
        return jwt.encode({'iss': ISSUER, 'sub': self.name, 'aud': audience,
                           'iat': now, 'exp': now + MAX_LIFETIME}, self.private_key,
                          algorithm='EdDSA', headers={'kid': self.name})

    def verify(self, token: str) -> str:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get('kid')
            if header.get('alg') != 'EdDSA' or not isinstance(kid, str):
                raise ValueError('Invalid algorithm or identity')
            key = self.public_keys.get(kid)
            if key is None:
                raise ValueError('Unknown signing key')
            claims = jwt.decode(token, key, algorithms=['EdDSA'], audience=self.name,
                                issuer=ISSUER, options={'require': ['sub','aud','iss','iat','exp']})
            if claims['sub'] != kid or not 0 < claims['exp'] - claims['iat'] <= MAX_LIFETIME:
                raise ValueError('Invalid subject or lifetime')
            return kid
        except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
            raise HTTPException(401, 'Invalid service identity') from None


def bearer_token(request: Request) -> str:
    parts = request.headers.get('authorization', '').split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        raise HTTPException(401, 'Bearer token required', headers={'WWW-Authenticate': 'Bearer'})
    return parts[1]


def service_identity(request: Request) -> str:
    return request.app.state.service_security.verify(request.headers.get('x-service-token', ''))


def require_service(request: Request, allowed: set[str]) -> str:
    caller = service_identity(request)
    if caller not in allowed:
        raise HTTPException(403, 'Service operation not permitted')
    return caller