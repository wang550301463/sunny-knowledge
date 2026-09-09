"""Live Docker tests: real Keycloak Authorization Code + PKCE through gateway."""
import importlib.util
import json
from pathlib import Path
import unittest
import uuid

import httpx

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('oidc_login', ROOT/'scripts/oidc_login.py')
login_module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(login_module)

class IdentityRegression(unittest.TestCase):
    def test_real_keycloak_pkce_then_authenticated_iam(self):
        config=json.loads((ROOT/'.local/test-env.json').read_text())
        base=config['public_url']
        response=httpx.get(base+'/healthz')
        self.assertEqual(response.status_code,200)
        tokens=login_module.login(base,config['admin_username'],config['admin_password'])
        headers={'Authorization':'Bearer '+tokens['access_token']}
        response=httpx.get(base+'/api/v1/me',headers=headers)
        self.assertEqual(response.status_code,200,'Real issuer token must authenticate through auth/IAM')
        data=response.json()
        self.assertIn('platform_admin',str(data))
        response=httpx.post(base+'/api/v1/spaces',headers=headers,json={'name':'regression-'+str(uuid.uuid4())})
        self.assertEqual(response.status_code,201,response.text)
        response=httpx.get(base+'/api/v1/spaces',headers=headers)
        self.assertEqual(response.status_code,200)
        self.assertGreaterEqual(len(response.json()['items']),1)

    def test_untrusted_identity_headers_do_not_authorize(self):
        config=json.loads((ROOT/'.local/test-env.json').read_text())
        response=httpx.get(config['public_url']+'/api/v1/me',headers={'X-Role':'admin','X-User-ID':'admin','X-Principal-ID':'admin','X-Service-Token':'forged'})
        self.assertEqual(response.status_code,401)
        response=httpx.get(config['public_url']+'/internal/v1/principals/admin')
        self.assertIn(response.status_code,[403,404])

if __name__=='__main__': unittest.main()