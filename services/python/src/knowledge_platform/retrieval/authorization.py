"""Current identity + complete policy fingerprint checks before any ES recall."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from pydantic import ValidationError

from .projection import clauses_for, policy_fingerprint
from .schemas import Policy, unavailable


@dataclass
class AuthorizationGuard:
    authorizer: object
    token: str
    principal: object

    @classmethod
    async def begin(cls, authorizer, token, space_ids):
        principal = await authorizer.resolve(token)
        guard = cls(authorizer, token, principal)
        for space in space_ids:
            decision = await authorizer.require(token,"read",space)
            guard.epoch(decision.auth_epoch)
        return guard

    def epoch(self, epoch):
        if epoch != self.principal.auth_epoch:
            raise unavailable("authorization_changed", "Authorization changed during the operation")

    async def finish(self):
        current=await self.authorizer.resolve(self.token)
        self.epoch(current.auth_epoch)
        if current.id != self.principal.id or set(current.subjects) != set(self.principal.subjects):
            raise unavailable("authorization_changed", "Authorization changed during the operation")


class PolicyCache:
    def __init__(self, ttl=5, max_entries=200000):
        self.ttl,self.max_entries=ttl,max_entries
        self.values={}
        self.lock=asyncio.Lock()

    async def allowed(self, guard, records):
        expected={}
        for record in records:
            for value in record["policies"]:
                policy=Policy.model_validate(value)
                expected[(policy.space_id,policy.resource_id)]=None
        epoch=guard.principal.auth_epoch
        async with self.lock:
            timestamp=time.monotonic()
            self.values={key:value for key,value in self.values.items() if key[0] == epoch and value[0] > timestamp}
            missing=[key for key in sorted(expected) if (epoch,*key) not in self.values]
            for offset in range(0,len(missing),1000):
                batch=missing[offset:offset+1000]
                response=await guard.authorizer.request("POST","/internal/v1/policies/batch","iam",json={"resources":[{"space_id":s,"resource_id":r} for s,r in batch]})
                try:
                    payload=response.json()
                    guard.epoch(payload["auth_epoch"])
                    items=payload["items"]
                    if len(items) != len(batch) or {(p["space_id"],p["resource_id"]) for p in items} != set(batch):
                        raise ValueError
                    for item in items:
                        key=(item["space_id"],item["resource_id"])
                        if item["found"] is True:
                            policy=Policy.model_validate(item["policy"])
                            guard.epoch(policy.auth_epoch)
                            if (policy.space_id,policy.resource_id) != key:
                                raise ValueError
                        elif item["found"] is False:
                            policy=None
                        else:
                            raise ValueError
                        self.values[(epoch,*key)]=(timestamp+self.ttl,policy)
                except (KeyError,TypeError,ValueError,ValidationError):
                    raise unavailable("invalid_policy_response", "Current IAM policy snapshot unavailable") from None
            # Read one coherent cache snapshot; cache eviction occurs only after this request's copy.
            current={key:self.values[(epoch,*key)][1] for key in expected}
            if len(self.values) > self.max_entries:
                self.values.clear()
        subjects=set(guard.principal.subjects)
        allowed=[]
        for record in records:
            policies=[current[(p["space_id"],p["resource_id"])] for p in record["policies"]]
            if any(p is None for p in policies):
                continue
            fingerprint=policy_fingerprint(policies)
            if fingerprint != policy_fingerprint(record["policies"]):
                continue
            if all(subjects.intersection(clause) for clause in clauses_for(policies)):
                allowed.append(fingerprint)
        await guard.finish()
        return sorted(set(allowed))