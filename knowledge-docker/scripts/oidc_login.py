"""Exercise the same Authorization Code + PKCE flow as the WebUI; no password grant."""
import base64
import hashlib
from html.parser import HTMLParser
import secrets
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}
        self.in_login_form = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'form' and attrs.get('id') == 'kc-form-login':
            self.action = attrs.get('action')
            self.in_login_form = True
        if tag == 'input' and self.in_login_form and attrs.get('type') == 'hidden' and attrs.get('name'):
            self.fields[attrs['name']] = attrs.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.in_login_form = False


def login(base_url, username, password):
    base = base_url.rstrip('/')
    issuer = base + '/idp/realms/knowledge'
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state = secrets.token_urlsafe(24)
    redirect = base + '/auth/callback'
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        page = client.get(issuer + '/protocol/openid-connect/auth', params={
            'client_id':'knowledge-web', 'redirect_uri':redirect, 'response_type':'code',
            'scope':'openid profile email knowledge:read knowledge:write knowledge:feedback',
            'state':state, 'code_challenge':challenge, 'code_challenge_method':'S256'})
        form = LoginForm()
        form.feed(page.text)
        if page.status_code != 200 or not form.action:
            raise RuntimeError(f'Keycloak login form unavailable (HTTP {page.status_code})')
        action = urljoin(base, form.action)
        if urlsplit(action)[:2] != urlsplit(base)[:2]:
            raise RuntimeError('Refusing to send credentials to a different origin')
        response = client.post(action, data=form.fields | {'username':username, 'password':password})
        callback = response.headers.get('location', '')
        parsed = urlsplit(callback)
        params = parse_qs(parsed.query)
        if response.status_code not in {302,303} or parsed[:3] != urlsplit(redirect)[:3] or params.get('state') != [state] or 'code' not in params:
            raise RuntimeError(f'Keycloak did not complete expected login callback (HTTP {response.status_code})')
        response = client.post(issuer + '/protocol/openid-connect/token', data={
            'grant_type':'authorization_code','client_id':'knowledge-web','redirect_uri':redirect,
            'code':params['code'][0],'code_verifier':verifier})
        if response.status_code != 200:
            raise RuntimeError(f'Keycloak code exchange failed (HTTP {response.status_code})')
        result = response.json()
        if not result.get('access_token'):
            raise RuntimeError('Keycloak response has no access token')
        return result