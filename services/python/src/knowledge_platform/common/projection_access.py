"""Machine admission for canonical projections, independent of business valid time."""

from dataclasses import dataclass

from fastapi import HTTPException


class RevisionNotVisible(HTTPException):
    """Only a coherent, successful canonical decision can construct this signal."""

    def __init__(self, auth_epoch: int):
        super().__init__(403, "Canonical revision is not currently visible")
        self.auth_epoch = auth_epoch


@dataclass
class ProjectionAccess:
    auth: object
    token: str
    principal: object

    @classmethod
    async def begin(cls, auth, token, page_id, revision_id):
        access = cls(auth, token, await auth.resolve(token))
        await access.check([(page_id, revision_id)])
        return access

    async def check(self, references):
        keys = sorted(set(references))
        denied = False
        for offset in range(0, len(keys), 100):
            batch = keys[offset : offset + 100]
            response = await self.auth.request(
                "POST",
                "/internal/v1/pages/authorize",
                "knowledge",
                self.token,
                {"pages": [{"page_id": p, "revision_id": r} for p, r in batch],
                 "include_historical": True},
            )
            try:
                decisions = response.json()["decisions"]
                if not isinstance(decisions, list) or len(decisions) != len(batch):
                    raise ValueError
                if {(d["page_id"], d["revision_id"]) for d in decisions} != set(batch):
                    raise ValueError
                for decision in decisions:
                    if type(decision["allowed"]) is not bool:
                        raise ValueError
                    if "authorized" in decision and type(decision["authorized"]) is not bool:
                        raise ValueError
                    if decision.get("authorized") is True:
                        if not isinstance(decision.get("space_id"), str) or not decision["space_id"]:
                            raise ValueError
                    elif decision["allowed"] is False:
                        denied = True
                    else:
                        raise ValueError
            except (KeyError, TypeError, ValueError):
                raise HTTPException(503, "Invalid canonical projection authorization") from None
            await self.finish()
        if denied:
            raise RevisionNotVisible(self.principal.auth_epoch)

    async def finish(self):
        current = await self.auth.resolve(self.token)
        if (
            current.auth_epoch != self.principal.auth_epoch
            or current.id != self.principal.id
            or set(current.subjects) != set(self.principal.subjects)
            or getattr(current, "channel_context", None)
            != getattr(self.principal, "channel_context", None)
        ):
            raise HTTPException(503, "Authorization changed during projection")
