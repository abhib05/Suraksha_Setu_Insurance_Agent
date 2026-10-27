"""A small JSON-logic evaluator with three-valued (Kleene) results.

A missing fact evaluates to UNKNOWN instead of raising or guessing. UNKNOWN spreads through
comparisons and arithmetic, while `and` / `or` short-circuit the usual way: `or(true, ?)` is
true and `and(false, ?)` is false, so a rule only comes back unknown when the missing fact
could actually change the answer. Every var that was missing on the way is reported.

Dates are ISO strings (YYYY-MM-DD); custom operators:
  days_between(a, b)          -> (b - a) in days
  cover_end(paid_on, amount, annual) -> last day (31 May) of the cover year a premium buys
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any


class _Unknown:
    def __repr__(self) -> str:
        return "UNKNOWN"

    def __bool__(self) -> bool:
        raise TypeError("UNKNOWN has no truth value; use is_unknown()")


UNKNOWN = _Unknown()


def is_unknown(v: Any) -> bool:
    return v is UNKNOWN


@dataclass
class Evaluation:
    value: Any
    missing: list[str] = field(default_factory=list)


def _date(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def cover_end(paid_on: Any, amount: Any, annual: Any) -> str:
    """PMJJBY/PMSBY cover years run 1 June - 31 May. A full-premium debit in May is the
    auto-debit renewal for the year starting that June; any debit from June onwards buys
    cover to the next 31 May; a pro-rata debit Jan-May buys cover to this 31 May."""
    d = _date(paid_on)
    renewal = d.month == 5 and float(amount) >= float(annual)
    year = d.year + 1 if (d.month >= 6 or renewal) else d.year
    return date(year, 5, 31).isoformat()


def _cmp(op: str):
    def f(a: Any, b: Any) -> Any:
        if isinstance(a, str) and isinstance(b, (int, float)):
            a = float(a)
        if isinstance(b, str) and isinstance(a, (int, float)):
            b = float(b)
        return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b,
                ">=": a >= b}[op]  # fmt: skip

    return f


_SIMPLE = {
    **{op: _cmp(op) for op in ("==", "!=", "<", "<=", ">", ">=")},
    "in": lambda a, b: a in b,
    "+": lambda *a: sum(a),
    "-": lambda a, b: a - b,
    "days_between": lambda a, b: (_date(b) - _date(a)).days,
    "cover_end": cover_end,
}


def evaluate(logic: Any, facts: dict[str, Any]) -> Evaluation:
    missing: list[str] = []
    value = _eval(logic, facts, missing)
    return Evaluation(value, sorted(set(missing)))


def _eval(node: Any, facts: dict[str, Any], missing: list[str]) -> Any:
    if isinstance(node, list):
        return [_eval(n, facts, missing) for n in node]
    if not isinstance(node, dict):
        return node
    if len(node) != 1:
        raise ValueError(f"logic node must have exactly one operator: {node}")
    op, args = next(iter(node.items()))
    if not isinstance(args, list):
        args = [args]

    if op == "var":
        name = args[0]
        if facts.get(name) in (None, ""):
            missing.append(name)
            return UNKNOWN
        return facts[name]
    if op == "and":
        unknown = False
        for a in args:
            v = _eval(a, facts, missing)
            if is_unknown(v):
                unknown = True
            elif not v:
                return False
        return UNKNOWN if unknown else True
    if op == "or":
        unknown = False
        for a in args:
            v = _eval(a, facts, missing)
            if is_unknown(v):
                unknown = True
            elif v:
                return True
        return UNKNOWN if unknown else False
    if op in ("!", "not"):
        v = _eval(args[0], facts, missing)
        return v if is_unknown(v) else not v
    if op == "if":
        cond = _eval(args[0], facts, missing)
        if is_unknown(cond):
            return UNKNOWN
        return _eval(args[1] if cond else args[2], facts, missing)
    if op not in _SIMPLE:
        raise ValueError(f"unsupported operator {op!r}")
    vals = [_eval(a, facts, missing) for a in args]
    if any(is_unknown(v) for v in vals):
        return UNKNOWN
    return _SIMPLE[op](*vals)
