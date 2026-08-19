"""Schema-less aliases used only to construct flexible test fixtures."""

from __future__ import annotations

from typing import TypeAlias, TypeVar

from typing_extensions import TypeAliasType

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue = TypeAliasType("JsonValue", JsonScalar | list["JsonValue"] | dict[str, "JsonValue"])
JsonObject: TypeAlias = dict[str, JsonValue]
MapKey = TypeVar("MapKey")
MapValue = TypeVar("MapValue")
Map: TypeAlias = dict[MapKey, MapValue]
