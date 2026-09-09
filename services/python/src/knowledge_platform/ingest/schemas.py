"""Strict ingest requests; secrets are write-only and never reflected in errors."""
from __future__ import annotations

import json
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

MAX_FILE_BYTES = 4_000_000


class IngestError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


def validate_path(value: str) -> str:
    if (not value or len(value) > 2048 or '\\' in value or ':' in value
            or any(ord(c) < 32 for c in value)
            or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('Expected a relative POSIX path')
    return value


def validate_remote(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError('Invalid Git remote')
    try:
        u = urlsplit(value)
        port = u.port
    except ValueError:
        raise ValueError('Invalid Git remote') from None
    if (u.scheme not in {'http', 'https', 'ssh'} or not u.hostname or not u.path
            or u.username is not None or u.password is not None or u.query or u.fragment
            or any(ord(c) < 32 for c in value) or '\\' in value
            or not re.fullmatch(r'[a-zA-Z0-9.\-:\[\]]+', u.netloc)
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError('Only HTTP(S)/SSH URLs without URL credentials are accepted')
    return value


def validate_config(kind: str, config: dict) -> dict:
    if kind == 'git':
        if set(config) != {'url', 'ref'}:
            raise ValueError('Git config requires url and ref')
        validate_remote(config['url'])
        ref = config['ref']
        if (not isinstance(ref, str) or len(ref) > 512
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/\-]*', ref)
                or '..' in ref or ref.endswith(('/', '.')) or '//' in ref):
            raise ValueError('Invalid Git ref')
    else:
        if set(config) != {'path', 'content'}:
            raise ValueError('Upload config requires path and content')
        validate_path(config['path'])
        body = config['content']
        if not isinstance(body, str):
            raise ValueError('Upload content must be exact UTF-8 text')
        try:
            data = body.encode('utf-8')
        except UnicodeError:
            raise ValueError('Upload must be valid UTF-8') from None
        if len(data) > MAX_FILE_BYTES or any(c < 32 and c not in (9, 10, 12, 13) for c in data):
            raise ValueError('Upload exceeds byte limit or contains binary content')
        if kind == 'ticket':
            try:
                obj = json.loads(body)
                if not isinstance(obj, dict):
                    raise ValueError
            except (ValueError, TypeError):
                raise ValueError('Ticket content must be an exact JSON object') from None
    return config


class Credential(StrictModel):
    username: str | None = Field(default=None, max_length=256)
    password: SecretStr | None = None
    ssh_private_key: SecretStr | None = None
    ssh_known_hosts: str | None = Field(default=None, max_length=64_000)

    @field_validator('username')
    @classmethod
    def username_safe(cls, value):
        if value is not None and (not value or any(ord(c) < 32 for c in value)):
            raise ValueError('Invalid credential username')
        return value

    def plain(self):
        return {k: v.get_secret_value() if isinstance(v, SecretStr) else v
                for k, v in self.__dict__.items() if v is not None}


class SourceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    space_id: str = Field(min_length=1, max_length=512)
    kind: Literal['git', 'markdown', 'ticket']
    config: dict
    credential: Credential | None = None

    @model_validator(mode='after')
    def configured(self):
        validate_config(self.kind, self.config)
        if self.kind != 'git' and self.credential is not None:
            raise ValueError('Only Git sources accept credentials')
        return self


class SourceUpdate(StrictModel):
    base_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=256)
    config: dict
    credential: Credential | None = None


class VersionRequest(StrictModel):
    base_version: int = Field(ge=1)