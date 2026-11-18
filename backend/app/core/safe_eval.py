"""Restricted expression evaluator (AST whitelist). Used for calculator tools, workflow conditions
and data transforms. No attribute access, no imports, no comprehension side effects, bounded size."""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
        ast.GtE: operator.ge, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
_FUNCS = {"len": len, "min": min, "max": max, "abs": abs, "round": round, "sum": sum, "sqrt": math.sqrt,
          "lower": lambda s: str(s).lower(), "upper": lambda s: str(s).upper(), "str": str, "int": int,
          "float": float, "bool": bool, "contains": lambda a, b: b in a, "startswith": lambda a, b: str(a).startswith(b)}


class UnsafeExpression(ValueError):
    pass


def safe_eval(expr: str, names: dict[str, Any] | None = None, max_len: int = 500) -> Any:
    if len(expr) > max_len:
        raise UnsafeExpression("expression too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpression(f"invalid expression: {exc.msg}") from exc
    env = names or {}

    def ev(n: ast.AST) -> Any:
        match n:
            case ast.Expression(body=b):
                return ev(b)
            case ast.Constant(value=v) if isinstance(v, int | float | str | bool) or v is None:
                return v
            case ast.Name(id=i):
                if i in env:
                    return env[i]
                if i in ("True", "False", "None"):
                    return {"True": True, "False": False, "None": None}[i]
                raise UnsafeExpression(f"unknown name '{i}'")
            case ast.BinOp(left=l, op=o, right=r) if type(o) in _BIN:
                lv, rv = ev(l), ev(r)
                if isinstance(o, ast.Pow) and (abs(rv) > 100 or abs(lv) > 1e6):
                    raise UnsafeExpression("exponent too large")
                if isinstance(o, ast.Mult) and isinstance(lv, str | list) and isinstance(rv, int) and rv > 1000:
                    raise UnsafeExpression("repetition too large")
                return _BIN[type(o)](lv, rv)
            case ast.UnaryOp(op=o, operand=x) if type(o) in _UN:
                return _UN[type(o)](ev(x))
            case ast.BoolOp(op=ast.And(), values=vs):
                return all(ev(v) for v in vs)
            case ast.BoolOp(op=ast.Or(), values=vs):
                return any(ev(v) for v in vs)
            case ast.Compare(left=l, ops=ops, comparators=cs):
                cur = ev(l)
                for o, c in zip(ops, cs, strict=True):
                    if type(o) not in _CMP:
                        raise UnsafeExpression("comparison not allowed")
                    nxt = ev(c)
                    if not _CMP[type(o)](cur, nxt):
                        return False
                    cur = nxt
                return True
            case ast.IfExp(test=t, body=b, orelse=e):
                return ev(b) if ev(t) else ev(e)
            case ast.Call(func=ast.Name(id=f), args=args, keywords=[]) if f in _FUNCS:
                return _FUNCS[f](*[ev(a) for a in args])
            case ast.Subscript(value=v, slice=s):
                return ev(v)[ev(s)]
            case ast.List(elts=es) | ast.Tuple(elts=es):
                return [ev(e) for e in es]
            case ast.Dict(keys=ks, values=vs):
                return {ev(k): ev(v) for k, v in zip(ks, vs, strict=True) if k is not None}
        raise UnsafeExpression(f"disallowed syntax: {type(n).__name__}")

    return ev(tree)
