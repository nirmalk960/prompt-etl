"""Runtime fence for prompt-generated Python.

The AST check rejects obvious escapes. This module is the backstop that still
holds when generated code reaches a file reader, a writer, or a module pandas
or polars happens to hold (such as ``os``). DataFrame methods keep working;
reading or writing files from user code does not.
"""

from __future__ import annotations

import types
from contextlib import contextmanager
from typing import Any, Iterator

# Attribute names that must never be followed, on a library or on a frame.
BLOCKED_ATTRS = frozenset({
    "os", "sys", "subprocess", "shutil", "socket", "pathlib", "ctypes",
    "pickle", "marshal", "importlib", "builtins", "posix", "nt", "requests",
    "httpx", "urllib", "http", "ftplib", "ssl", "signal", "multiprocessing",
    "pdb", "code", "codeop", "eval", "query", "system", "popen",
})

# Method and function names that move bytes onto or off the machine.
_IO_PREFIXES = ("read_", "scan_", "write_", "sink_")
_IO_NAMES = frozenset({
    "to_csv", "to_pickle", "to_excel", "to_json", "to_html", "to_sql",
    "to_feather", "to_parquet", "to_clipboard", "to_hdf", "to_stata",
    "to_xml", "to_orc", "to_latex", "open", "remove", "unlink", "rmdir",
    "execl", "execv", "spawn", "fork", "eval", "query",
})


class SandboxError(RuntimeError):
    pass


def is_io_name(name: str) -> bool:
    """File readers and writers. Dunder names are not included."""
    if not name or name.startswith("_"):
        return False
    lower = name.lower()
    return lower in _IO_NAMES or lower.startswith(_IO_PREFIXES)


def blocked_attr(name: str) -> bool:
    if not name or name.startswith("_"):
        return True
    lower = name.lower()
    return lower in BLOCKED_ATTRS or is_io_name(name)


def _blocked(name: str):
    def _refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise SandboxError(f"{name} is not allowed")

    return _refuse


class ModuleGuard:
    """Proxy in front of ``pandas`` or ``polars``. Submodules are not exposed."""

    def __init__(self, module: types.ModuleType):
        object.__setattr__(self, "_module", module)

    def __getattr__(self, name: str) -> Any:
        if blocked_attr(name):
            raise SandboxError(f"{name} is not allowed")
        module = object.__getattribute__(self, "_module")
        value = getattr(module, name)
        if isinstance(value, types.ModuleType):
            raise SandboxError(f"{name} is not allowed")
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        raise SandboxError("library objects are read-only")


def module_guard(module: types.ModuleType) -> ModuleGuard:
    return ModuleGuard(module)


@contextmanager
def seal_frame_io() -> Iterator[None]:
    """Disable file readers and writers on frame classes for the duration of exec.

    Restores the originals before the engine itself writes the result parquet.
    """
    import pandas as pd
    import polars as pl

    saved: list[tuple[type, str, Any]] = []
    classes: list[type] = [pd.DataFrame, pd.Series]
    for cls_name in ("DataFrame", "LazyFrame", "Series"):
        cls = getattr(pl, cls_name, None)
        if isinstance(cls, type):
            classes.append(cls)

    seen: set[tuple[type, str]] = set()
    for cls in classes:
        for name in dir(cls):
            if not is_io_name(name):
                continue
            key = (cls, name)
            if key in seen:
                continue
            try:
                original = getattr(cls, name)
            except Exception:
                continue
            try:
                setattr(cls, name, _blocked(name))
            except (AttributeError, TypeError):
                continue
            seen.add(key)
            saved.append((cls, name, original))
    try:
        yield
    finally:
        for cls, name, original in saved:
            try:
                setattr(cls, name, original)
            except (AttributeError, TypeError):
                pass
