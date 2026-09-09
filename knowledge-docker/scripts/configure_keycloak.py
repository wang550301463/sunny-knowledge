#!/usr/bin/env python3
"""Reconcile approved local realm scopes without replacing users or credentials."""
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]


def request(method,url,body=None,token=None,form=False):
    headers={}
    if body is not None:
        body=(urlencode(body) if form else json.dumps(body)).encode()
        headers['Content-Type']='application/x-www-form-urlencoded' if form else 'application/json'
    if token: headers['Authorization']='Bearer '+token
    with urlopen(Request(url,data=body,headers=headers,method=method),timeout=20) as response:
        data=response.read()
        return json.loads(data) if data else None


def configure():
    values=dict(line.split('=',1) for line in (ROOT/'.env').read_text().splitlines() if line and not line.startswith('#'))
    base=values['PUBLIC_WEB_URL'].rstrip('/')+'/idp'
    auth=request('POST',base+'/realms/master/protocol/openid-connect/token',{'grant_type':'password','client_id':'admin-cli','username':'bootstrap','password':values['KEYCLOAK_ADMIN_PASSWORD']},form=True)
    token=auth['access_token']
    spec=importlib.util.spec_from_file_location('knowledge_init',ROOT/'scripts/init.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    scopes=module.client_scopes()
    api=base+'/admin/realms/knowledge'
    existing={s['name']:s for s in request('GET',api+'/client-scopes',token=token)}
    for scope in scopes:
        if scope['name'] not in existing:
            request('POST',api+'/client-scopes',scope,token)
    existing={s['name']:s for s in request('GET',api+'/client-scopes',token=token)}
    clients=request('GET',api+'/clients',token=token)
    for client in clients:
        if client['clientId'] not in {'knowledge-web','knowledge-cli'}: continue
        for scope in scopes:
            request('PUT',api+'/clients/'+client['id']+'/default-client-scopes/'+existing[scope['name']]['id'],token=token)
    # Keep future clean imports consistent; preserve all credential material.
    realm_path=ROOT/'.local/knowledge-realm.json'
    realm=json.loads(realm_path.read_text());realm['clientScopes']=scopes
    for client in realm['clients']:
        client['defaultClientScopes']=[scope['name'] for scope in scopes]
    realm_path.write_text(json.dumps(realm,indent=2)+'\n')
    print('Keycloak scopes reconciled; users and credentials retained.')

if __name__=='__main__': configure()