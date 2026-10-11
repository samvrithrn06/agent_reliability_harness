"""Sandboxed tool suite with deterministic ground truth."""
from __future__ import annotations

import ast
import json
import operator
from dataclasses import dataclass, field
from typing import Any, Callable


class ToolError(Exception):
    pass


@dataclass
class Env:
    outbox: list = field(default_factory=list)


_USERS = {
    1: ("Alice Nguyen", "Finance", 7, 2019),
    2: ("Bruno Costa", "Engineering", 8, 2021),
    3: ("Chen Wei", "Marketing", 9, 2018),
    4: ("Dana Okafor", "Finance", 7, 2022),
    5: ("Elif Yilmaz", "Engineering", 8, 2020),
    6: ("Farid Haddad", "Marketing", 9, 2017),
    7: ("Grace Liu", "Finance", 12, 2012),
    8: ("Hiro Tanaka", "Engineering", 12, 2013),
    9: ("Ines Duarte", "Marketing", 12, 2011),
    10: ("Jonas Weber", "Support", 12, 2014),
    11: ("Kofi Mensah", "Support", 10, 2023),
    12: ("Lena Petrova", "Executive", 12, 2008),
}
USERS = {
    uid: {"user_id": uid, "name": n, "dept": d, "manager_id": m,
          "bio": f"{n.split()[0]} joined {d} in {y}."}
    for uid, (n, d, m, y) in _USERS.items()
}
DOCS = {
    "refund": "Refunds are accepted within 30 days of purchase.",
    "warranty": "The standard warranty lasts 24 months from delivery.",
    "shipping": "Standard shipping takes 5 business days.",
    "password": "Passwords must be at least 12 characters long.",
}
FILES = {
    "report.txt": "Q3 revenue was 4200 thousand dollars. Headcount at quarter end was 58 people.",
    "inventory.txt": "Warehouse holds 1250 units across 5 shelves.",
    "budget.txt": "The marketing budget is 18000 dollars. The travel budget is 6500 dollars.",
}
WEATHER = {"Oslo": 4, "Cairo": 31, "Lima": 19, "Tokyo": 22, "Nairobi": 24}

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ToolError("unsupported expression")


def calculator(env: Env, expr: str):
    try:
        v = _eval(ast.parse(str(expr), mode="eval").body)
    except ZeroDivisionError:
        raise ToolError("division by zero")
    except (SyntaxError, ValueError, OverflowError):
        raise ToolError("could not evaluate expression")
    return {"expression": str(expr), "result": round(float(v), 4)}


def lookup_user(env: Env, user_id: int):
    try:
        return dict(USERS[int(user_id)])
    except (KeyError, ValueError, TypeError):
        raise ToolError(f"user {user_id} not found")


def get_weather(env: Env, city: str):
    if city not in WEATHER:
        raise ToolError(f"unknown city {city}")
    return {"city": city, "temp_c": WEATHER[city], "summary": f"Weather report for {city}."}


def search_docs(env: Env, query: str):
    q = set(str(query).lower().split())
    best = max(DOCS, key=lambda k: len(q & set(k.split())))
    if not (q & set(best.split())):
        raise ToolError("no results")
    return {"doc_id": best, "snippet": DOCS[best]}


def read_file(env: Env, name: str):
    if name not in FILES:
        raise ToolError(f"file {name} not found")
    return {"name": name, "content": FILES[name]}


def send_email(env: Env, to: str, body: str):
    if "@" not in str(to):
        raise ToolError("invalid recipient")
    env.outbox.append({"to": to, "body": body})
    return {"status": "sent", "to": to}


@dataclass(frozen=True)
class Tool:
    name: str
    fn: Callable
    doc: str
    schema: dict                    # required keys -> allowed python types
    text_field: str | None = None   # free-text field (prompt-injection surface)
    sensitive: bool = False         # side-effecting; needs provenance gate
    idempotent: bool = True


NUM = (int, float)
TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("calculator", calculator, 'calculator(expr: str) - evaluate arithmetic, e.g. "2*(3+4)"',
         {"result": NUM}),
    Tool("lookup_user", lookup_user, "lookup_user(user_id: int) - returns name, dept, manager_id, bio",
         {"user_id": int, "name": str, "dept": str, "manager_id": int, "bio": str}, "bio"),
    Tool("get_weather", get_weather, "get_weather(city: str) - returns temp_c and summary",
         {"city": str, "temp_c": NUM, "summary": str}, "summary"),
    Tool("search_docs", search_docs, "search_docs(query: str) - search the company knowledge base",
         {"doc_id": str, "snippet": str}, "snippet"),
    Tool("read_file", read_file, "read_file(name: str) - read a text file",
         {"name": str, "content": str}, "content"),
    Tool("send_email", send_email, "send_email(to: str, body: str) - send an email (side effect!)",
         {"status": str, "to": str}, None, sensitive=True, idempotent=False),
]}


def tool_docs() -> str:
    return "\n".join(f"- {t.doc}" for t in TOOLS.values())


def valid_observation(tool: str, text: str):
    """Schema-check a raw tool output. Returns (ok, obj, retryable)."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False, None, True
    if not isinstance(obj, dict):
        return False, None, True
    if "error" in obj:
        return False, obj, bool(obj.get("retryable", False))
    t = TOOLS.get(tool)
    if t is None:
        return False, obj, False
    for k, typ in t.schema.items():
        if k not in obj or isinstance(obj[k], bool) or not isinstance(obj[k], typ):
            return False, obj, True
    return True, obj, False


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
