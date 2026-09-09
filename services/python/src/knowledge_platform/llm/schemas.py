from __future__ import annotations

import json
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from knowledge_platform.common.config import Settings

Capability = Literal['chat', 'embedding', 'rerank']
TextInput = Annotated[str, Field(min_length=1, max_length=65536)]


class LLMSettings(Settings):
    service_name: str = 'llm'
    credential_encryption_key: SecretStr = SecretStr('')
    llm_max_request_bytes: int = Field(2_000_000, ge=1024, le=10_000_000)
    llm_allow_http_providers: bool = False
    llm_metadata_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60)


class LLMError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)


class ModelConfig(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    provider_model: str = Field(min_length=1, max_length=200, pattern=r'^[^\s\x00-\x1f]+$')
    base_url: str = Field(min_length=1, max_length=2048)
    capability: Capability
    dimensions: int | None = Field(None, ge=1, le=16384)
    request_dimensions: bool = False
    max_input_chars: int = Field(131072, ge=1, le=1_000_000)
    max_batch_size: int = Field(64, ge=1, le=256)
    max_output_tokens: int = Field(4096, ge=1, le=32768)
    max_response_bytes: int = Field(8_000_000, ge=1024, le=32_000_000)
    timeout_seconds: float = Field(60, ge=.01, le=180, allow_inf_nan=False)
    max_retries: int = Field(2, ge=0, le=3)
    concurrency_per_replica: int = Field(4, ge=1, le=100)
    max_queue_per_replica: int = Field(16, ge=0, le=200)
    chat_token_parameter: Literal['max_completion_tokens', 'max_tokens'] = 'max_completion_tokens'

    @field_validator('base_url')
    @classmethod
    def valid_url(cls, value):
        url = urlsplit(value)
        if (url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(ord(c) < 33 for c in value)):
            raise ValueError('Base URL must be HTTP(S) without credentials, query, or fragment')
        return value.rstrip('/')

    @model_validator(mode='after')
    def capability_dimensions(self):
        if self.capability == 'embedding' and self.dimensions is None:
            raise ValueError('Embedding dimensions must be configured explicitly')
        if self.capability != 'embedding' and (self.dimensions is not None or self.request_dimensions):
            raise ValueError('Dimensions apply only to embeddings')
        return self


class ModelCreate(ModelConfig):
    credential: SecretStr | None = Field(None, repr=False)

    @field_validator('credential')
    @classmethod
    def valid_credential(cls, value):
        if value is not None:
            secret = value.get_secret_value()
            if not 1 <= len(secret) <= 8192 or any(ord(c) < 33 or ord(c) > 126 for c in secret):
                raise ValueError('Credential must be a nonempty printable token')
        return value


class ModelUpdate(StrictModel):
    base_configuration_id: UUID
    config: ModelConfig
    credential: SecretStr | None = Field(None, repr=False)
    _validate_credential = field_validator('credential')(ModelCreate.valid_credential.__func__)

    @model_validator(mode='after')
    def no_null_rotation(self):
        if 'credential' in self.model_fields_set and self.credential is None:
            raise ValueError('Omit credential to preserve it; provide a value to rotate')
        return self


class ModelState(StrictModel):
    base_configuration_id: UUID
    state: Literal['active', 'disabled', 'retired']


class TestRequest(StrictModel):
    configuration_id: UUID
    test_tools: bool = False
    test_stream: bool = False


class FunctionCall(StrictModel):
    name: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    arguments: str = Field(max_length=65536)

    @field_validator('arguments')
    @classmethod
    def json_arguments(cls, value):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError('Tool arguments must be a JSON object')
        return value


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=200, pattern=r'^[a-zA-Z0-9_-]+$')
    type: Literal['function'] = 'function'
    function: FunctionCall


class Message(StrictModel):
    role: Literal['system', 'developer', 'user', 'assistant', 'tool']
    content: str | None = Field(None, max_length=131072)
    tool_calls: list[ToolCall] | None = Field(None, max_length=20)
    tool_call_id: str | None = Field(None, min_length=1, max_length=200)

    @model_validator(mode='after')
    def structure(self):
        if self.role == 'tool' and not self.tool_call_id:
            raise ValueError('Tool messages require tool_call_id')
        if self.role != 'tool' and self.tool_call_id:
            raise ValueError('Only tool messages have tool_call_id')
        if self.tool_calls and self.role != 'assistant':
            raise ValueError('Only assistant messages have tool_calls')
        if self.content is None and not self.tool_calls:
            raise ValueError('Message requires content or tool_calls')
        return self


class ToolFunction(StrictModel):
    name: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    description: str | None = Field(None, max_length=4000)
    parameters: dict
    strict: bool | None = None

    @field_validator('parameters')
    @classmethod
    def object_schema(cls, value):
        if value.get('type') != 'object' or len(json.dumps(value)) > 32768:
            raise ValueError('Bounded JSON object schema required')
        return value


class ToolDefinition(StrictModel):
    type: Literal['function'] = 'function'
    function: ToolFunction


class ChatRequest(StrictModel):
    configuration_id: UUID
    messages: list[Message] = Field(min_length=1, max_length=100)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=20)
    max_output_tokens: int | None = Field(None, ge=1, le=32768)
    temperature: float | None = Field(None, ge=0, le=2, allow_inf_nan=False)
    stream: bool = False
    response_format: dict | None = None

    @field_validator('response_format')
    @classmethod
    def format_schema(cls, value):
        if value is not None:
            if value.get('type') not in {'json_object', 'json_schema'} or len(json.dumps(value)) > 32768:
                raise ValueError('Bounded JSON response format required')
            if set(value) - {'type', 'json_schema'}:
                raise ValueError('Unknown response format field')
        return value


class EmbeddingRequest(StrictModel):
    configuration_id: UUID
    input: list[TextInput] = Field(min_length=1, max_length=256)


class RerankRequest(StrictModel):
    configuration_id: UUID
    query: TextInput
    documents: list[TextInput] = Field(min_length=1, max_length=256)
    top_n: int | None = Field(None, ge=1, le=256)

    @model_validator(mode='after')
    def top_count(self):
        if self.top_n is not None and self.top_n > len(self.documents):
            raise ValueError('top_n exceeds document count')
        return self


def validate_limits(config: ModelConfig, body):
    if isinstance(body, ChatRequest):
        chars = len(json.dumps(body.model_dump(mode='json'), ensure_ascii=False))
        if body.max_output_tokens and body.max_output_tokens > config.max_output_tokens:
            raise LLMError(422, 'model_limit', 'Output token limit exceeded')
    else:
        items = body.input if isinstance(body, EmbeddingRequest) else body.documents
        if len(items) > config.max_batch_size:
            raise LLMError(422, 'model_limit', 'Batch limit exceeded')
        chars = sum(map(len, items)) + (len(body.query) if isinstance(body, RerankRequest) else 0)
    if chars > config.max_input_chars:
        raise LLMError(422, 'model_limit', 'Input character limit exceeded')