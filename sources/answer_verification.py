"""Narrow, bounded deterministic checks. No exec/eval, file or network access.

Unknown contracts abstain. Results are derived from task inputs, never benchmarks.
"""
import ast
from datetime import date, timedelta
import json
import operator
import re


def typed_equal(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(typed_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(typed_equal(x, y) for x, y in zip(a, b))
    return a == b


def matches(answer, expected):
    try:
        return typed_equal(json.loads(answer), expected)
    except (ValueError, TypeError):
        return False


class Unsupported(ValueError):
    pass


class _Continue(Exception):
    pass


class BoundedPython:
    """Tiny integer/list language interpreter, deliberately NOT a Python sandbox."""
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
    compare = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Gt: operator.gt,
               ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le}

    def __init__(self):
        self.env = {}
        self.steps = 0

    def tick(self):
        self.steps += 1
        if self.steps > 4000:
            raise Unsupported("instruction budget exceeded")

    def bounded(self, value):
        if type(value) is int and abs(value) > 10**12:
            raise Unsupported("integer out of bounds")
        if isinstance(value, (list, tuple, range)) and len(value) > 500:
            raise Unsupported("collection out of bounds")
        return value

    def expr(self, node):
        self.tick()
        if isinstance(node, ast.Constant) and type(node.value) in (int, bool, type(None)):
            return self.bounded(node.value)
        if isinstance(node, ast.Name) and node.id in self.env:
            return self.env[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return self.bounded([self.expr(e) for e in node.elts])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            value = self.expr(node.operand)
            if isinstance(node.op, ast.Not):
                return not value
            if type(value) is not int:
                raise Unsupported("integer required")
            return self.bounded(-value if isinstance(node.op, ast.USub) else value)
        if isinstance(node, ast.BinOp) and type(node.op) in self.binary:
            left, right = self.expr(node.left), self.expr(node.right)
            if type(left) is not int or type(right) is not int:
                raise Unsupported("only integer arithmetic is supported")
            return self.bounded(self.binary[type(node.op)](left, right))
        if isinstance(node, ast.IfExp):
            return self.expr(node.body if self.expr(node.test) else node.orelse)
        if isinstance(node, ast.Compare):
            left = self.expr(node.left)
            for op, rhs in zip(node.ops, node.comparators):
                right = self.expr(rhs)
                if type(op) not in self.compare:
                    raise Unsupported("unsupported comparison")
                if not self.compare[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            args = [self.expr(a) for a in node.args]
            if node.func.id == "enumerate" and len(args) == 1 and isinstance(args[0], list):
                return list(enumerate(args[0]))
            if node.func.id == "range" and 1 <= len(args) <= 3 and all(type(a) is int for a in args):
                return self.bounded(range(*args))
            if node.func.id in ("len", "sum") and len(args) == 1 and isinstance(args[0], list):
                if node.func.id == "sum" and not all(type(x) is int for x in args[0]):
                    raise Unsupported("integer list required")
                return self.bounded(len(args[0]) if node.func.id == "len" else sum(args[0]))
        raise Unsupported(f"unsupported expression: {type(node).__name__}")

    def assign(self, node, value):
        if isinstance(node, ast.Name):
            self.env[node.id] = self.bounded(value)
        elif isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) == len(value):
            for item, val in zip(node.elts, value):
                self.assign(item, val)
        else:
            raise Unsupported("unsupported assignment")

    def block(self, statements):
        for node in statements:
            self.tick()
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                self.assign(node.targets[0], self.expr(node.value))
            elif isinstance(node, ast.AugAssign) and type(node.op) in self.binary:
                # Reuse the same bounds and type checks as a normal binary expression.
                self.assign(node.target, self.expr(ast.BinOp(left=node.target, op=node.op, right=node.value)))
            elif isinstance(node, ast.If):
                self.block(node.body if self.expr(node.test) else node.orelse)
            elif isinstance(node, ast.For) and not node.orelse:
                values = self.expr(node.iter)
                if not isinstance(values, (list, range)):
                    raise Unsupported("bounded sequence required")
                for value in list(values):
                    self.assign(node.target, value)
                    try:
                        self.block(node.body)
                    except _Continue:
                        continue
            elif isinstance(node, ast.Continue):
                raise _Continue()
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                if (isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
                        and call.func.attr == "append" and len(call.args) == 1 and not call.keywords):
                    target = self.env.get(call.func.value.id)
                    if not isinstance(target, list):
                        raise Unsupported("append target must be a list")
                    value = self.expr(call.args[0])
                    if type(value) not in (int, bool, type(None)) or len(target) >= 500:
                        raise Unsupported("bounded scalar append required")
                    target.append(value)
                else:
                    raise Unsupported("method not allowed")
            else:
                raise Unsupported(f"unsupported statement: {type(node).__name__}")

    def run(self, code):
        if len(code) > 12000:
            raise Unsupported("source too large")
        tree = ast.parse(code)
        if len(list(ast.walk(tree))) > 800:
            raise Unsupported("syntax tree too large")
        self.block(tree.body)
        return self.env


def verification(query, evidence):
    """Recognize only explicit task contracts, otherwise return supported=False."""
    unknown = {"supported": False, "method": "abstain"}
    if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
        return unknown
    try:
        # Code tracing: one source, explicit Python semantics, explicit output variables.
        if "Python" in query and any(word in query for word in ("推演", "trace")) and len(evidence) == 1:
            namespace = BoundedPython().run(evidence[0])
            requested = query.split("输出", 1)[-1] if "输出" in query else ""
            keys = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*[（(]", requested)
            if keys and all(key in namespace for key in keys) and len(set(keys)) == len(keys) and "JSON" in query:
                return {"supported": True, "method": "bounded_python_trace", "expected": {key: namespace[key] for key in keys}}
        # Explicit start/holiday contract; do not guess locale, calendars or inclusion.
        count = re.search(r"数\s*(\d+)\s*个工作日", query)
        if (count and "不含start" in query and "周六日" in query and "holiday" in query
                and "输出due（YYYY-MM-DD字符串）。" in query and len(evidence) == 1):
            match = re.fullmatch(r"\s*start=(\d{4}-\d{2}-\d{2});\s*holiday=(\d{4}-\d{2}-\d{2})\s*", evidence[0])
            if match and 1 <= int(count.group(1)) <= 366:
                current = date.fromisoformat(match.group(1))
                holiday = date.fromisoformat(match.group(2))
                remaining = int(count.group(1))
                while remaining:
                    current += timedelta(days=1)
                    if current.weekday() < 5 and current != holiday:
                        remaining -= 1
                return {"supported": True, "method": "business_calendar", "expected": {"due": current.isoformat()}}
    except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError, RecursionError, _Continue) as exc:
        return {**unknown, "reason": type(exc).__name__}
    return unknown
