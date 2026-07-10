"""Per-engine validators for LLM-generated code.

The goal is defense-in-depth: even though pandas/polars engines exec in a
restricted namespace, we still want to reject obviously-malicious or
out-of-scope code before invoking exec().

Each validator returns a short reason string on failure, or None on success.
"""

from __future__ import annotations

import ast
import re

from ..engines.base import EngineKind

# Imports, dunder access, and built-ins that suggest an escape attempt.
_PYTHON_BANNED_NAMES = {
    "__import__", "open", "eval", "exec", "compile", "input",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
}
_PYTHON_BANNED_MODULES = {
    "os", "sys", "subprocess", "shutil", "socket", "requests",
    "urllib", "http", "pathlib", "io", "ctypes", "pickle", "marshal",
}


def validate_python(code: str, *, allowed_top_assign: str = "result") -> str | None:
    """Lightweight AST check used by pandas and polars engines."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return f"syntax error: {e.msg}"

    has_result_assign = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            return "import statements are not allowed"
        if isinstance(node, ast.ImportFrom):
            return "import statements are not allowed"
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                return f"dunder attribute access not allowed: {node.attr}"
        if isinstance(node, ast.Name):
            if node.id in _PYTHON_BANNED_NAMES:
                return f"use of '{node.id}' is not allowed"
            if node.id in _PYTHON_BANNED_MODULES:
                return f"use of module '{node.id}' is not allowed"
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == allowed_top_assign:
                    has_result_assign = True

    if not has_result_assign:
        return f"code must assign a value to `{allowed_top_assign}`"

    return None


_DUCKDB_FORBIDDEN = re.compile(
    r"\b(ATTACH|INSTALL|LOAD|COPY|PRAGMA|EXPORT|IMPORT|CREATE\s+SECRET|SET\s+|"
    r"INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE)\b",
    re.IGNORECASE,
)


def validate_sql(sql: str) -> str | None:
    s = sql.strip().rstrip(";").strip()
    if not s:
        return "empty SQL"
    if not re.match(r"^(WITH|SELECT)\b", s, re.IGNORECASE):
        return "SQL must start with SELECT or WITH"
    if _DUCKDB_FORBIDDEN.search(s):
        return "SQL contains forbidden keyword (mutations / DDL / pragmas)"
    if ";" in s:
        return "multi-statement SQL is not allowed"
    return None


def validate(engine: EngineKind, code: str) -> str | None:
    if engine in {"pandas", "polars"}:
        return validate_python(code)
    if engine == "duckdb":
        return validate_sql(code)
    if engine == "spark":
        # Phase 2 will plug PySpark validator here.
        return None
    return f"unknown engine: {engine}"


def normalize_python_result_assignment(code: str) -> str:
    """Coerce common LLM output into `result = ...` form.

    Typical LLM responses for aggregate-like prompts end with an expression such
    as `df.groupby(...).agg(...).reset_index()` but forget `result =`.
    This helper rewrites the final expression statement into an assignment when
    no explicit assignment to `result` exists yet.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return code

    has_result_assign = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "result":
                    has_result_assign = True
                    break
        if has_result_assign:
            break

    if has_result_assign or not tree.body:
        return code

    # Common pattern: model assigns to a temp variable (e.g. `agg_df = ...`)
    # but never maps it to `result`.
    for stmt in reversed(tree.body):
        if isinstance(stmt, ast.Assign):
            target = stmt.targets[0] if stmt.targets else None
            if isinstance(target, ast.Name):
                tree.body.append(
                    ast.Assign(
                        targets=[ast.Name(id="result", ctx=ast.Store())],
                        value=ast.Name(id=target.id, ctx=ast.Load()),
                    )
                )
                ast.fix_missing_locations(tree)
                try:
                    return ast.unparse(tree)
                except Exception:
                    return code

    last = tree.body[-1]
    if isinstance(last, ast.Expr):
        tree.body[-1] = ast.Assign(
            targets=[ast.Name(id="result", ctx=ast.Store())],
            value=last.value,
        )
        ast.fix_missing_locations(tree)
        try:
            return ast.unparse(tree)
        except Exception:
            return code

    return code


# ---------------------------------------------------------------------------
# Code extraction: strip markdown fences if the model returned them.
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:python|sql)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_code(raw: str) -> str:
    """Pull code out of markdown fences if present; otherwise return raw."""
    m = _FENCE.search(raw)
    if m:
        return m.group(1).strip()
    return raw.strip()
