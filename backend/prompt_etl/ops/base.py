"""Operation registry types.

An operation ("op") is a deterministic, configurable pipeline step. Each op
declares its parameters so the UI can render a settings form, and either:

* ``engine="sql"``: ``compile(config, input_schemas)`` returns one DuckDB
  ``SELECT`` over the views ``input_0``, ``input_1`` (``df`` = ``input_0``) and
  ``input_0_rn`` (input_0 plus a ``__rn`` row-order column); or
* ``engine="python"``: ``run(dataframes, config)`` returns a pandas DataFrame
  plus optional metrics; ``output_schema`` predicts its columns for preflight.

``engine="prompt"`` marks the free-form custom box, which only has prompt mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal

if TYPE_CHECKING:
    import duckdb
    import pandas as pd

ParamType = Literal[
    "column", "columns", "number", "integer", "text", "select", "multiselect",
    "boolean", "expression", "list",
]

# (column name, DuckDB type name)
Column = tuple[str, str]
Schema = list[Column]


class OpError(ValueError):
    """A user-facing configuration or data error raised by an op."""


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    type: ParamType
    required: bool = False
    default: Any = None
    # select/multiselect: (value, label) pairs
    options: tuple[tuple[str, str], ...] = ()
    help: str = ""
    # column pickers: which input's columns to offer (0 = left/only, 1 = right)
    input: int = 0
    # list: the fields of each item
    fields: tuple["Param", ...] = ()
    # Only shown when param `show_if[0]` has one of the values in `show_if[1]`.
    show_if: tuple[str, tuple[Any, ...]] | None = None
    placeholder: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
        }
        if self.default is not None:
            out["default"] = self.default
        if self.options:
            out["options"] = [{"value": v, "label": lbl} for v, lbl in self.options]
        if self.help:
            out["help"] = self.help
        if self.input:
            out["input"] = self.input
        if self.fields:
            out["fields"] = [f.to_json() for f in self.fields]
        if self.show_if:
            out["show_if"] = {"param": self.show_if[0], "values": list(self.show_if[1])}
        if self.placeholder:
            out["placeholder"] = self.placeholder
        return out


@dataclass
class PyResult:
    df: "pd.DataFrame"
    metrics: dict[str, Any] | None = None


@dataclass(frozen=True)
class OpSpec:
    id: str
    label: str
    category: str
    description: str
    params: tuple[Param, ...] = ()
    engine: Literal["sql", "python", "prompt"] = "sql"
    inputs: int = 1
    compile: Callable[[dict[str, Any], list[Schema]], str] | None = None
    # Runs inside the sandboxed connection before the output is written; raise
    # OpError to fail the step (e.g. validation rules in "fail" mode).
    post_check: Callable[["duckdb.DuckDBPyConnection", dict[str, Any], list[Schema]], None] | None = None
    run: Callable[[list["pd.DataFrame"], dict[str, Any]], PyResult] | None = None
    output_schema: Callable[[dict[str, Any], list[Schema]], Schema | None] | None = None
    # Output columns depend on data values (e.g. pivot): preflight can't predict them.
    dynamic_schema: bool = False
    # Save the step output as a dataset (dashboards, predictions).
    persist_output: bool = False
    preset_prompt: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def supports_prompt(self) -> bool:
        return self.engine != "python"

    @property
    def supports_configure(self) -> bool:
        return self.engine != "prompt"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            "engine": self.engine,
            "inputs": self.inputs,
            "params": [p.to_json() for p in self.params],
            "supports_prompt": self.supports_prompt,
            "supports_configure": self.supports_configure,
            "preset_prompt": self.preset_prompt,
            "persist_output": self.persist_output,
        }


def opts(*values: str | tuple[str, str]) -> tuple[tuple[str, str], ...]:
    """Build select options; bare strings use a title-cased label."""
    out: list[tuple[str, str]] = []
    for v in values:
        if isinstance(v, tuple):
            out.append(v)
        else:
            out.append((v, v.replace("_", " ").capitalize()))
    return tuple(out)
