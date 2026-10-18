"""Evaluate small, explicitly sourced scalar calculations without ``eval``.

The caller supplies units, assumptions, and the operating-mode validity. This
module checks neither dimensions nor datasheet truth and does not prove a mode.
Only a caller-declared ``valid`` calculation can return a usable result.
``usable`` describes the arithmetic under that declaration, not independent
verification of physical parameters, numeric literals, or a real working point.
``conditional`` results are labeled trial_result and can support further analysis
under the same explicit assumptions. The caller must carry those conditions;
this module does not track dependencies or promote a trial to an actual working
point. Invalid/unknown modes and missing or non-finite inputs produce no value.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import math
from collections.abc import Mapping


def _has_source(source) -> bool:
    if isinstance(source, str):
        return bool(source.strip())
    if isinstance(source, Mapping):
        return any(_has_source(value) for value in source.values())
    if isinstance(source, (list, tuple)):
        return any(_has_source(value) for value in source)
    return False


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a known finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _calculate(node: ast.AST, inputs: dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _calculate(node.body, inputs)
    if isinstance(node, ast.Constant):
        return _number(node.value, "Numeric literal")
    if isinstance(node, ast.Name):
        if node.id not in inputs:
            raise ValueError(f"Missing sourced input: {node.id}")
        return inputs[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _calculate(node.operand, inputs)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            raise ValueError("Only +, -, *, /, and ** operators are allowed")
        left = _calculate(node.left, inputs)
        right = _calculate(node.right, inputs)
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        elif isinstance(node.op, ast.Div):
            result = left / right
        else:
            result = math.pow(left, right)
        return _number(result, "Intermediate result")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise ValueError("Only direct calls to min, max, abs, and sqrt are allowed")
        name = node.func.id
        if name not in {"min", "max", "abs", "sqrt"}:
            raise ValueError(f"Function is not allowed: {name}")
        if not node.args or (name in {"abs", "sqrt"} and len(node.args) != 1):
            raise ValueError(f"Invalid argument count for {name}")
        values = [_calculate(argument, inputs) for argument in node.args]
        if name == "min":
            result = min(values)
        elif name == "max":
            result = max(values)
        elif name == "abs":
            result = abs(values[0])
        else:
            result = math.sqrt(values[0])
        return _number(result, "Function result")
    raise ValueError(f"Expression construct is not allowed: {type(node).__name__}")


def evaluate(record: Mapping) -> dict:
    """Return the original record plus result, usable, status, and any reason.

    Expected record fields are ``id``, ``inputs`` (name -> value/unit/source),
    ``expression``, ``result_unit``, ``assumptions``, and ``validity``
    (status/reason). Inputs actually referenced by the expression must be known
    finite numbers with a non-empty source reference. Unused context entries
    are preserved without becoming calculation prerequisites. Expressions use input names and numeric
    literals, arithmetic, parentheses, and min/max/abs/sqrt only.

    A source reference is checked for presence, not verified against the source.
    Numeric literals remain allowed; neither their truth nor their physical
    applicability is established by this tool. The investigation and review
    must establish the provenance and meaning of a physical calculation.
    Supplied input/formula/assumption/validity fields are not rewritten. Generated
    result fields from an earlier evaluation are cleared before reevaluation.
    """
    if not isinstance(record, Mapping):
        raise TypeError("Calculation record must be a mapping")
    output = deepcopy(dict(record))
    for generated in ("result", "trial_result", "usable", "status", "reason"):
        output.pop(generated, None)
    output.update(result=None, usable=False, status="not_computed")
    try:
        validity = record.get("validity")
        if not isinstance(validity, Mapping):
            raise ValueError("validity with an explicit status and reason is required")
        mode = validity.get("status")
        if not isinstance(mode, str) or mode not in {"valid", "conditional", "invalid", "unknown"}:
            raise ValueError("validity.status must be valid, conditional, invalid, or unknown")
        if not isinstance(validity.get("reason"), str) or not validity["reason"].strip():
            raise ValueError("validity.reason is required; operating mode is caller-supplied")
        if mode in {"invalid", "unknown"}:
            output["reason"] = f"Operating-mode validity is {mode}; no numeric result computed"
            return output
        expression = record.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("expression must be a non-empty string")
        if len(expression) > 4096:
            raise ValueError("Expression exceeds the 4096-character limit")
        if not isinstance(record.get("result_unit"), str):
            raise ValueError("result_unit must be a string")
        tree = ast.parse(expression.strip(), mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 256:
            raise ValueError("Expression exceeds the 256-node limit")
        inputs = record.get("inputs")
        if not isinstance(inputs, Mapping):
            raise ValueError("inputs must map each variable to value, unit, and source")
        # A function name in min(x, y) is not an input. A separate bare variable
        # named min is still an input, so distinguish the individual AST nodes.
        call_names = {id(node.func) for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        used_names = {node.id for node in ast.walk(tree)
                      if isinstance(node, ast.Name) and id(node) not in call_names}
        numbers = {}
        for name in sorted(used_names):
            if name not in inputs:
                raise ValueError(f"Missing sourced input: {name}")
            entry = inputs[name]
            if not isinstance(entry, Mapping):
                raise ValueError(f"Input {name} must contain value, unit, and source")
            if not _has_source(entry.get("source")):
                raise ValueError(f"Missing source for input: {name}")
            if not isinstance(entry.get("unit"), str):
                raise ValueError(f"Input {name} needs a unit string; use empty string if dimensionless")
            numbers[name] = _number(entry.get("value"), f"Input {name}")
        value = _calculate(tree, numbers)
    except (ValueError, SyntaxError, ArithmeticError) as exc:
        output["reason"] = str(exc)
        return output
    if mode == "conditional":
        output.update(trial_result=value, status="conditional",
                      reason="Conditional result; further analysis may reuse it with the same explicit conditions. "
                             "The caller must retain those conditions; this is not an established working point.")
    else:
        output.update(result=value, usable=True, status="evaluated")
    return output
