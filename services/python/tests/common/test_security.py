import json
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
import jwt
import pytest

from knowledge_platform.common.security import ServiceSecurity


def pair():
    private = Ed25519PrivateKey.generate()
    return (private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode())


def identities():
    left,lp=pair(); right,rp=pair()
    keys={'gateway':lp,'knowledge':rp}
    return ServiceSecurity('gateway',left,keys), ServiceSecurity('knowledge',right,keys)


def test_service_token_only_valid_for_intended_recipient():
    gateway, knowledge = identities()
    token = gateway.issue('knowledge')
    assert knowledge.verify(token) == 'gateway'
    with pytest.raises(HTTPException): gateway.verify(token)


def test_other_service_cannot_impersonate_gateway_even_with_public_keys():
    gateway,knowledge=identities()
    now=int(time.time())
    token=jwt.encode({'iss':'knowledge-services','sub':'gateway','aud':'knowledge','iat':now,'exp':now+60},knowledge.private_key,algorithm='EdDSA',headers={'kid':'gateway'})
    with pytest.raises(HTTPException): knowledge.verify(token)


@pytest.mark.parametrize('overrides',[{'exp':0},{'exp':int(time.time())+3600},{'sub':'auth'},{'iss':'attacker'},{'iat':int(time.time())+60}])
def test_reject_expired_overlong_mismatched_or_future_tokens(overrides):
    gateway,knowledge=identities(); now=int(time.time())
    payload={'iss':'knowledge-services','sub':'gateway','aud':'knowledge','iat':now,'exp':now+60}|overrides
    token=jwt.encode(payload,gateway.private_key,algorithm='EdDSA',headers={'kid':'gateway'})
    with pytest.raises(HTTPException): knowledge.verify(token)


def test_unknown_audience_cannot_receive_credentials():
    gateway,_=identities()
    with pytest.raises(ValueError): gateway.issue('https://outside.example')