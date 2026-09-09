import base64
import secrets
import pytest
from knowledge_platform.common.secrets import SecretBox


def box(): return SecretBox(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())


def test_encrypts_secret_with_nondeterministic_nonce_and_bound_record_identity():
    cipher=box()
    left=cipher.encrypt('provider-secret','model:a')
    right=cipher.encrypt('provider-secret','model:a')
    assert left!=right and 'provider-secret' not in left
    assert cipher.decrypt(left,'model:a')=='provider-secret'
    with pytest.raises(ValueError):cipher.decrypt(left,'model:b')


def test_wrong_key_or_corruption_never_returns_partial_plaintext():
    cipher=box(); token=cipher.encrypt('sensitive-value','bot:1')
    with pytest.raises(ValueError):box().decrypt(token,'bot:1')
    with pytest.raises(ValueError):cipher.decrypt(token[:-8]+'AAAAAAAA','bot:1')
    with pytest.raises(ValueError):cipher.decrypt('not-ciphertext','bot:1')


def test_rejects_weak_or_malformed_master_key_and_hides_repr():
    for key in ['password','',base64.urlsafe_b64encode(b'weak').decode()]:
        with pytest.raises(ValueError):SecretBox(key)
    key=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    assert key not in repr(SecretBox(key))