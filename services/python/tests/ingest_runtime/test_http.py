import pytest
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from knowledge_platform.common.security import ServiceSecurity
from knowledge_platform.ingest.app import create_app
from knowledge_platform.ingest.config import IngestSettings
from .test_postgres import store, Machine, Internal
from .test_storage import ObjectServer
from knowledge_platform.ingest.storage import S3Store

@pytest.fixture
def security():
    keys={name:Ed25519PrivateKey.generate() for name in ('gateway','ingest','agent')}
    public={name:key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode() for name,key in keys.items()}
    return {name:ServiceSecurity(name,key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),public) for name,key in keys.items()}

async def test_public_routes_require_signed_gateway_and_live_bearer(store,security):
    db,auth,box=store
    app=create_app(database=db,authorizer=auth,security=security['ingest'],machine=Machine(),internal=Internal(),secret_box=box,storage=S3Store(ObjectServer(),'raw'),settings=IngestSettings(),initialize_schema=False)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://ingest') as client:
        assert (await client.get('/healthz')).status_code==200
        assert (await client.get('/api/v1/sources')).status_code==401
        headers={'X-Service-Token':security['gateway'].issue('ingest'),'Authorization':'Bearer alice'}
        response=await client.post('/api/v1/sources',headers=headers,json={'name':'guide','space_id':'space','kind':'markdown','config':{'path':'guide.md','content':'# Original\r\n'}})
        assert response.status_code==201,response.text
        source=response.json()
        preview=await client.post('/api/v1/sources/'+source['id']+'/preview',headers=headers,json={'base_version':1})
        assert preview.status_code==200,preview.text
        assert preview.json()['files'][0]['size']==12
        first=await client.post('/api/v1/sources/'+source['id']+'/sync',headers=headers,json={'base_version':1})
        second=await client.post('/api/v1/sources/'+source['id']+'/sync',headers=headers,json={'base_version':1})
        assert first.status_code==202,first.text
        assert first.json()['id']==second.json()['id']
        auth.disabled.add('alice')
        assert (await client.get('/api/v1/tasks/'+first.json()['id'],headers=headers)).status_code==403

async def test_invalid_request_does_not_echo_credentials(store,security):
    db,auth,box=store
    app=create_app(database=db,authorizer=auth,security=security['ingest'],machine=Machine(),internal=Internal(),secret_box=box,storage=S3Store(ObjectServer(),'raw'),settings=IngestSettings(),initialize_schema=False)
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://ingest') as client:
        headers={'X-Service-Token':security['gateway'].issue('ingest'),'Authorization':'Bearer alice'}
        result=await client.post('/api/v1/sources',headers=headers,json={'name':'repo','space_id':'space','kind':'git','config':{'url':'https://u:supersecret@host/repo','ref':'HEAD'}})
        assert result.status_code==422
        assert 'supersecret' not in result.text