#!/usr/bin/env python3
"""Generate private, idempotent local deployment configuration. Never print secrets."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess

SERVICES = ('gateway','iam','auth','channel','knowledge','ingest','retrieval','llm','graphiti','agent','mcp')
DATABASES = tuple(s for s in SERVICES if s != 'gateway') + ('keycloak','temporal','temporal_visibility')
ADMIN_ID = '01992200-0000-7000-8000-000000000001'

def write_private(path: Path, data: str):
    with path.open('x', encoding='utf-8') as handle:
        handle.write(data)
    path.chmod(0o600)

def initialize(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    env = root / '.env'
    local = root / '.local'
    if env.exists():
        if not all((local / name).is_file() for name in ('service-public-keys.json','knowledge-realm.json','init-databases.sql','test-env.json','s3.json')):
            raise SystemExit('Existing configuration is incomplete; restore .local from backup. No credentials overwritten.')
        print('Local configuration already initialized; existing credentials retained.')
        return
    local.mkdir(mode=0o700, exist_ok=True)
    if any(local.iterdir()):
        raise SystemExit('Nonempty .local without .env; resolve interrupted initialization before retrying. No files overwritten.')
    os.chmod(local, 0o700)
    values = {
        'COMPOSE_PROJECT_NAME':'sunny-knowledge-v2', 'GATEWAY_PORT':'18180', 'POSTGRES_PORT':'15439',
        'PUBLIC_WEB_URL':'http://localhost:18180', 'POSTGRES_PASSWORD':secrets.token_urlsafe(32),
        'BOOTSTRAP_ADMIN_SUBJECT':ADMIN_ID, 'BOOTSTRAP_USERNAME':'admin',
        'BOOTSTRAP_PASSWORD':secrets.token_urlsafe(24), 'KEYCLOAK_ADMIN_PASSWORD':secrets.token_urlsafe(24),
        'CREDENTIAL_ENCRYPTION_KEY':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'NEO4J_PASSWORD':secrets.token_urlsafe(24), 'S3_ACCESS_KEY':secrets.token_hex(12),
        'S3_SECRET_KEY':secrets.token_urlsafe(32),
    }
    public_keys = {}
    for service in SERVICES:
        path = local / 'identities' / service
        path.mkdir(parents=True, mode=0o700)
        private = path / 'private.pem'
        subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(private)], check=True, capture_output=True)
        private.chmod(0o600)
        public_keys[service] = subprocess.run(['openssl','pkey','-in',str(private),'-pubout'],check=True,capture_output=True,text=True).stdout
    write_private(local/'service-public-keys.json', json.dumps(public_keys, indent=2)+'\n')
    sql = []
    test = {'public_url':values['PUBLIC_WEB_URL'],'admin_username':values['BOOTSTRAP_USERNAME'],'admin_password':values['BOOTSTRAP_PASSWORD'],'databases':{}}
    for service in DATABASES:
        password = secrets.token_urlsafe(32)
        values['DB_PASSWORD_'+service.upper()] = password
        db = 'knowledge_'+service
        sql.extend([f"CREATE USER {service} WITH PASSWORD '{password}';",f'CREATE DATABASE {db} OWNER {service};',f'REVOKE CONNECT ON DATABASE {db} FROM PUBLIC;',f'GRANT CONNECT ON DATABASE {db} TO {service};'])
        test['databases'][service] = f'postgresql://{service}:{password}@127.0.0.1:15439/{db}'
    # Temporal's visibility connection uses the same account as its primary DB.
    sql.append('GRANT CONNECT ON DATABASE knowledge_temporal_visibility TO temporal;')
    sql.append('ALTER DATABASE knowledge_temporal_visibility OWNER TO temporal;')
    write_private(local/'init-databases.sql', '\n'.join(sql)+'\n')
    scopes = [{'name':n,'protocol':'openid-connect','attributes':{'include.in.token.scope':'true','display.on.consent.screen':'true'},'protocolMappers':[]} for n in ('knowledge:read','knowledge:write','knowledge:feedback')]
    audience_mapper = {'name':'knowledge-api-audience','protocol':'openid-connect','protocolMapper':'oidc-audience-mapper','config':{'included.custom.audience':'knowledge-api','id.token.claim':'false','access.token.claim':'true'}}
    client_common = {'enabled':True,'publicClient':True,'standardFlowEnabled':True,'directAccessGrantsEnabled':False,'redirectUris':['http://localhost:18180/auth/callback','http://127.0.0.1:18180/auth/callback','http://localhost:3000/auth/callback','http://127.0.0.1:3000/auth/callback'],'webOrigins':['http://localhost:18180','http://127.0.0.1:18180'],'defaultClientScopes':['basic','profile','email','knowledge:read','knowledge:write','knowledge:feedback'],'protocolMappers':[audience_mapper],'attributes':{'pkce.code.challenge.method':'S256'}}
    realm = {'realm':'knowledge','enabled':True,'registrationAllowed':False,'resetPasswordAllowed':False,'sslRequired':'external','accessTokenLifespan':300,'clientScopes':scopes,'clients':[dict(client_common,clientId='knowledge-web'),dict(client_common,clientId='knowledge-cli')],'users':[{'id':ADMIN_ID,'username':values['BOOTSTRAP_USERNAME'],'enabled':True,'emailVerified':True,'email':'admin@knowledge.local','firstName':'Knowledge','lastName':'Administrator','credentials':[{'type':'password','value':values['BOOTSTRAP_PASSWORD'],'temporary':False}]}]}
    write_private(local/'knowledge-realm.json',json.dumps(realm,indent=2)+'\n')
    write_private(local/'s3.json',json.dumps({'identities':[{'name':'knowledge-storage','credentials':[{'accessKey':values['S3_ACCESS_KEY'],'secretKey':values['S3_SECRET_KEY']}],'actions':['Admin','Read','List','Tagging','Write']} ]},indent=2)+'\n')
    write_private(local/'test-env.json',json.dumps(test,indent=2)+'\n')
    write_private(env,'\n'.join(f'{key}={value}' for key,value in values.items())+'\n')
    print('Initialized private configuration in .env and .local. Credentials were not printed.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parents[1])
    initialize(parser.parse_args().directory.resolve())