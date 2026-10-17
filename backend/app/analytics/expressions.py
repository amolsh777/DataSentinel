"""Safe, vectorised boolean expressions for user-defined business rules.

User text is parsed with :mod:`ast` and evaluated by walking a whitelisted subset of
node types against pandas Series. Nothing is passed to ``eval``/``exec``; attribute
access, subscripts, lambdas, comprehensions and arbitrary calls are rejected.

Grammar (Python syntax subset)
------------------------------
* column names as bare identifiers (``revenue``) or in backticks (```unit price```)
* numbers, strings, ``True``/``False``
* arithmetic ``+ - * / %`` and unary ``-``
* comparisons ``== != < <= > >=``, chained comparisons, ``in`` / ``not in`` with a literal list
* ``and`` / ``or`` / ``not``
* functions: ``abs(x)``, ``isnull(x)``, ``notnull(x)``

Example: ``abs(revenue - quantity * unit_price * (1 - discount_pct / 100)) <= 0.01``

Missing values: a row is *not evaluable* when any referenced column is null in that
row (unless the expression explicitly uses ``isnull``/``notnull``); such rows are
neither passes nor violations and are reported separately.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

MAX_EXPRESSION_LENGTH = 500
MAX_NODES = 200

_BACKTICK = re.compile(r"`([^`]+)`")
_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
            ast.Div: operator.truediv, ast.Mod: operator.mod}
_CMP_OPS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
            ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
_FUNCS = {"abs", "isnull", "notnull"}
_NULL_FUNCS = {"isnull", "notnull"}


class ExpressionError(ValueError):
    """The expression is syntactically invalid or uses a disallowed construct."""


@dataclass(frozen=True)
class CompiledExpression:
    source: str
    tree: ast.Expression
    columns: tuple[str, ...]       # referenced dataset columns, in order of appearance
    aliases: dict[str, str]        # placeholder identifier -> real column name
    handles_nulls: bool            # uses isnull/notnull


def compile_expression(source: str) -> CompiledExpression:
    """Parse and validate an expression. Raises :class:`ExpressionError`."""
    if not isinstance(source, str) or not source.strip():
        raise ExpressionError("Expression must be a non-empty string.")
    if len(source) > MAX_EXPRESSION_LENGTH:
        raise ExpressionError(f"Expression exceeds {MAX_EXPRESSION_LENGTH} characters.")

    aliases: dict[str, str] = {}

    def _alias(match: re.Match[str]) -> str:
        key = f"__col{len(aliases)}__"
        aliases[key] = match.group(1)
        return key

    text = _BACKTICK.sub(_alias, source)
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Invalid syntax: {exc.msg}.") from None

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise ExpressionError("Expression is too complex.")

    columns: list[str] = []
    handles_nulls = False
    for node in nodes:
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise ExpressionError(
                    f"Only these functions are allowed: {', '.join(sorted(_FUNCS))}."
                )
            if len(node.args) != 1 or node.keywords:
                raise ExpressionError(f"{node.func.id}() takes exactly one argument.")
            handles_nulls |= node.func.id in _NULL_FUNCS
        elif isinstance(node, ast.Name):
            if node.id in _FUNCS or node.id in ("True", "False"):
                continue
            col = aliases.get(node.id, node.id)
            if col not in columns:
                columns.append(col)
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, str, bool)):
                raise ExpressionError("Only number, string and boolean literals are allowed.")
        elif isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)):
                    if not isinstance(comp, (ast.List, ast.Tuple)) or not all(
                        isinstance(e, ast.Constant) for e in comp.elts
                    ):
                        raise ExpressionError("'in' requires a literal list, e.g. x in ['A', 'B'].")
                elif type(op) not in _CMP_OPS:
                    raise ExpressionError(f"Comparison '{type(op).__name__}' is not allowed.")
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _BIN_OPS:
                raise ExpressionError(f"Operator '{type(node.op).__name__}' is not allowed.")
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
                raise ExpressionError(f"Operator '{type(node.op).__name__}' is not allowed.")
        elif isinstance(node, (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Load,
                               ast.List, ast.Tuple, ast.In, ast.NotIn,
                               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
                               ast.USub, ast.UAdd, ast.Not,
                               *tuple(_CMP_OPS))):
            continue
        else:
            raise ExpressionError(f"Construct '{type(node).__name__}' is not allowed.")

    if not columns:
        raise ExpressionError("Expression must reference at least one column.")
    return CompiledExpression(source, tree, tuple(columns), aliases, handles_nulls)


def _prepare_column(series: pd.Series) -> pd.Series:
    """Use numeric values when the column is predominantly numeric, else stripped strings."""
    if pd.api.types.is_bool_dtype(series) or pd.api.types.is_numeric_dtype(series):
        return series
    non_null = series.dropna()
    if non_null.empty:
        return series
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric[series.notna()].notna().mean() >= 0.9:
        return numeric  # unparseable strings become NaN -> row not evaluable
    return series.astype("string").str.strip()


def _as_bool(value: Any, index: pd.Index) -> pd.Series:
    if isinstance(value, pd.Series):
        return value.fillna(False).astype(bool)
    return pd.Series(bool(value), index=index)


def evaluate(expr: CompiledExpression, df: pd.DataFrame) -> dict[str, Any]:
    """Evaluate against ``df``.

    Returns ``{"passed": Series[bool], "evaluable": Series[bool]}``; violations are
    ``evaluable & ~passed``. Raises :class:`ExpressionError` for missing columns.
    """
    missing = [c for c in expr.columns if c not in df.columns]
    if missing:
        raise ExpressionError(f"Unknown column(s): {missing}.")
    cols = {c: _prepare_column(df[c]) for c in expr.columns}
    index = df.index

    def ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id == "True":
                return True
            if node.id == "False":
                return False
            return cols[expr.aliases.get(node.id, node.id)]
        if isinstance(node, ast.BinOp):
            try:
                return _BIN_OPS[type(node.op)](ev(node.left), ev(node.right))
            except TypeError:
                raise ExpressionError(
                    "Arithmetic on a non-numeric column (fewer than 90% of its values parse as numbers)."
                ) from None
        if isinstance(node, ast.UnaryOp):
            val = ev(node.operand)
            if isinstance(node.op, ast.Not):
                return ~_as_bool(val, index)
            return -val if isinstance(node.op, ast.USub) else +val
        if isinstance(node, ast.BoolOp):
            parts = [_as_bool(ev(v), index) for v in node.values]
            out = parts[0]
            for p in parts[1:]:
                out = (out & p) if isinstance(node.op, ast.And) else (out | p)
            return out
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            result = pd.Series(True, index=index)
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)):
                    values = [e.value for e in comp.elts]
                    s = left if isinstance(left, pd.Series) else pd.Series(left, index=index)
                    hit = s.isin(values)
                    part = hit if isinstance(op, ast.In) else ~hit
                    right = None
                else:
                    right = ev(comp)
                    try:
                        part = _CMP_OPS[type(op)](left, right)
                    except TypeError:
                        raise ExpressionError(
                            "Cannot compare values of incompatible types (e.g. text with a number)."
                        ) from None
                result = result & _as_bool(part, index)
                left = right
            return result
        if isinstance(node, ast.Call):
            arg = ev(node.args[0])
            name = node.func.id
            if name == "abs":
                return np.abs(arg)
            s = arg if isinstance(arg, pd.Series) else pd.Series(arg, index=index)
            return s.isna() if name == "isnull" else s.notna()
        raise ExpressionError(f"Construct '{type(node).__name__}' is not allowed.")  # pragma: no cover

    try:
        with np.errstate(all="ignore"):
            raw = ev(expr.tree)
    except ZeroDivisionError:
        raise ExpressionError("Division by zero in a literal.") from None

    passed = _as_bool(raw, index)
    if expr.handles_nulls:
        evaluable = pd.Series(True, index=index)
    else:
        evaluable = pd.concat([cols[c].notna() for c in expr.columns], axis=1).all(axis=1)
    return {"passed": passed & evaluable, "evaluable": evaluable}


__all__ = ["CompiledExpression", "ExpressionError", "compile_expression", "evaluate"]
