from __future__ import annotations

from sqlalchemy import select

from .models import Audit, CapabilityTest, Configuration, ModelRecord, new_id
from .schemas import LLMError, ModelConfig


class ModelStore:
    def __init__(self, database, secrets, registry, settings):
        self.database, self.secrets, self.registry, self.settings = database, secrets, registry, settings

    def check_config(self, config):
        adapter = self.registry.get(config.provider)
        if config.capability not in adapter.capabilities:
            raise LLMError(422, 'unsupported_capability', 'Provider adapter does not support this capability')
        if config.base_url.startswith('http:') and not self.settings.llm_allow_http_providers:
            raise LLMError(422, 'insecure_provider_url', 'Provider endpoint must use HTTPS')

    async def _test(self, session, configuration_id):
        return await session.scalar(select(CapabilityTest).where(CapabilityTest.configuration_id == configuration_id).order_by(CapabilityTest.created_at.desc(), CapabilityTest.id.desc()).limit(1))

    async def public(self, session, model, config):
        test = await self._test(session, config.id)
        return {'id': model.id, 'configuration_id': config.id, 'version': config.version,
                'state': model.state, **config.config, 'has_credential': config.credential_ciphertext is not None,
                'created_at': config.created_at.isoformat(), 'created_by': config.created_by,
                'test_state': test.test_state if test else 'untested',
                'capabilities': test.capabilities if test else {},
                'tested_at': test.created_at.isoformat() if test else None,
                'test_error_code': test.error_code if test else None}

    async def create(self, actor, body):
        config = ModelConfig.model_validate(body.model_dump(exclude={'credential'}))
        self.check_config(config)
        model_id, cid = new_id(), new_id()
        credential = body.credential.get_secret_value() if body.credential is not None else None
        async with self.database.session() as session, session.begin():
            model = ModelRecord(id=model_id)
            session.add(model)
            await session.flush()
            version = Configuration(id=cid, model_id=model_id, version=1, config=config.model_dump(),
                                    credential_ciphertext=self.secrets.encrypt(credential, cid) if credential else None, created_by=actor)
            session.add(version)
            await session.flush()
            model.configuration_id = cid
            session.add(Audit(model_id=model_id, configuration_id=cid, action='created', actor_id=actor,
                              details={'credential_supplied': credential is not None}))
            return await self.public(session, model, version)

    async def get(self, model_id):
        async with self.database.session() as session:
            model = await session.get(ModelRecord, model_id)
            if model is None:
                raise LLMError(404, 'model_not_found', 'Model was not found')
            config = await session.get(Configuration, model.configuration_id)
            return await self.public(session, model, config)

    async def list(self, *, capability=None, active=False, cursor=None, limit=50):
        async with self.database.session() as session:
            statement = select(ModelRecord, Configuration).join(Configuration, ModelRecord.configuration_id == Configuration.id).order_by(ModelRecord.id).limit(limit + 1)
            if capability:
                statement = statement.where(Configuration.config['capability'].astext == capability)
            if active:
                statement = statement.where(ModelRecord.state == 'active')
            if cursor:
                statement = statement.where(ModelRecord.id > cursor)
            rows = (await session.execute(statement)).all()
            return {'items': [await self.public(session, m, c) for m, c in rows[:limit]],
                    'next_cursor': rows[limit - 1][0].id if len(rows) > limit else None}

    async def versions(self, model_id, cursor=None, limit=50):
        async with self.database.session() as session:
            model = await session.get(ModelRecord, model_id)
            if model is None:
                raise LLMError(404, 'model_not_found', 'Model was not found')
            query = select(Configuration).where(Configuration.model_id == model_id).order_by(Configuration.version.desc()).limit(limit + 1)
            if cursor:
                query = query.where(Configuration.version < cursor)
            configs = list((await session.scalars(query)).all())
            return {'items': [await self.public(session, model, c) for c in configs[:limit]],
                    'next_cursor': configs[limit - 1].version if len(configs) > limit else None}

    async def update(self, actor, model_id, base_id, *, config=None, credential=None, state=None):
        if config is not None:
            self.check_config(config)
        async with self.database.session() as session, session.begin():
            model = await session.scalar(select(ModelRecord).where(ModelRecord.id == model_id).with_for_update())
            if model is None:
                raise LLMError(404, 'model_not_found', 'Model was not found')
            if model.configuration_id != str(base_id):
                raise LLMError(409, 'configuration_conflict', 'Model configuration has changed')
            if model.state == 'retired':
                raise LLMError(409, 'model_retired', 'Retired model cannot be changed')
            previous = await session.get(Configuration, model.configuration_id)
            cid = new_id()
            secret = credential.get_secret_value() if credential is not None else self.decrypt(previous)
            version = Configuration(id=cid, model_id=model_id, version=previous.version + 1,
                                    config=config.model_dump() if config is not None else previous.config,
                                    credential_ciphertext=self.secrets.encrypt(secret, cid) if secret else None, created_by=actor)
            session.add(version)
            await session.flush()
            model.configuration_id = cid
            if state is not None:
                model.state = state
            session.add(Audit(model_id=model_id, configuration_id=cid, action='state_changed' if state else 'updated', actor_id=actor,
                              details={'previous_configuration_id': previous.id, 'credential_rotated': credential is not None, 'state': model.state}))
            return await self.public(session, model, version)

    def decrypt(self, config):
        if config.credential_ciphertext is None:
            return None
        try:
            return self.secrets.decrypt(config.credential_ciphertext, config.id)
        except ValueError:
            raise LLMError(503, 'credential_unavailable', 'Model credential is unavailable') from None

    async def snapshot(self, configuration_id, capability):
        async with self.database.session() as session:
            row = (await session.execute(select(ModelRecord, Configuration).join(Configuration, ModelRecord.id == Configuration.model_id).where(Configuration.id == str(configuration_id)))).first()
            if row is None:
                raise LLMError(404, 'configuration_not_found', 'Model configuration was not found')
            model, config = row
            if model.state != 'active':
                raise LLMError(409, 'model_inactive', 'Model is not active')
            settings = ModelConfig.model_validate(config.config)
            if settings.capability != capability:
                raise LLMError(422, 'wrong_capability', 'Configuration capability does not match request')
            # Capacity follows the model's current configuration, even when invoking a pinned old version.
            current = await session.get(Configuration, model.configuration_id)
            limits = ModelConfig.model_validate(current.config)
            return model.id, settings, self.decrypt(config), limits

    async def record_test(self, actor, model_id, body, result):
        async with self.database.session() as session, session.begin():
            model = await session.scalar(select(ModelRecord).where(ModelRecord.id == model_id).with_for_update())
            if model is None or model.configuration_id != str(body.configuration_id):
                raise LLMError(409, 'configuration_conflict', 'Model configuration changed during the test')
            row = CapabilityTest(configuration_id=str(body.configuration_id), actor_id=actor, **result)
            session.add(row)
            session.add(Audit(model_id=model_id, configuration_id=str(body.configuration_id), action='tested', actor_id=actor,
                              details={'test_state': result['test_state'], 'error_code': result['error_code']}))
            await session.flush()
            return {'id': row.id, 'configuration_id': row.configuration_id, **result,
                    'tested_at': row.created_at.isoformat()}