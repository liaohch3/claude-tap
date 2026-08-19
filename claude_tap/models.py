"""Application-owned Pydantic models and explicit dynamic JSON boundaries."""

from __future__ import annotations

from collections.abc import ItemsView, Iterator, KeysView, Mapping, MutableMapping, ValuesView

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic import JsonValue as _JsonValue

_JSON_VALUE_ADAPTER = TypeAdapter(_JsonValue)


class ProviderPayload(BaseModel, MutableMapping[str, _JsonValue]):
    """Validated provider payload boundary with explicit mapping semantics.

    Provider APIs evolve independently of this project.  The model validates
    the top-level object and preserves unknown provider fields, while callers
    still interact through a normal mapping interface at protocol boundaries.
    Business code must convert payloads into a concrete domain model before
    relying on provider-specific fields.
    """

    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, _JsonValue] = Field(init=False)

    def __getitem__(self, key: str) -> _JsonValue:
        return self.__pydantic_extra__[key]

    def __setitem__(self, key: str, value: _JsonValue) -> None:
        if self.__pydantic_extra__ is None:
            object.__setattr__(self, "__pydantic_extra__", {})
        self.__pydantic_extra__[key] = _JSON_VALUE_ADAPTER.validate_python(value)

    def __delitem__(self, key: str) -> None:
        if self.__pydantic_extra__ is None:
            raise KeyError(key)
        del self.__pydantic_extra__[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.__pydantic_extra__ or {})

    def __len__(self) -> int:
        return len(self.__pydantic_extra__ or {})

    def get(self, key: str, default: _JsonValue = None) -> _JsonValue:
        return (self.__pydantic_extra__ or {}).get(key, default)

    def items(self) -> ItemsView[str, _JsonValue]:
        return (self.__pydantic_extra__ or {}).items()

    def keys(self) -> KeysView[str]:
        return (self.__pydantic_extra__ or {}).keys()

    def values(self) -> ValuesView[_JsonValue]:
        return (self.__pydantic_extra__ or {}).values()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ProviderPayload):
            return dict(self.items()) == dict(other.items())
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other)
        return NotImplemented

    @classmethod
    def from_mapping(cls, value: Mapping[str, _JsonValue] | "ProviderPayload") -> "ProviderPayload":
        if isinstance(value, cls):
            return value
        return cls.model_validate(dict(value))


class PromptToolModel(BaseModel):
    """Normalized tool metadata owned by the prompt snapshot feature."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    schema_: ProviderPayload = Field(default_factory=ProviderPayload, alias="schema")
    raw: ProviderPayload = Field(default_factory=ProviderPayload)

    @property
    def schema(self) -> ProviderPayload:
        """Return the provider tool schema without shadowing Pydantic internals."""
        return self.schema_


class PromptSnapshotModel(BaseModel):
    """Stable, serialized representation of a provider prompt snapshot."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    system_prompt: str = ""
    developer_prompt: str = ""
    user_message: str = ""
    tools: tuple[PromptToolModel, ...] = ()
    turn: int | None = None
    request_id: str = ""
    path: str = ""
    upstream_base_url: str = ""
    captured_at: str = ""
    raw_request_body: ProviderPayload = Field(default_factory=ProviderPayload)
