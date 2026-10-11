"""Agent-side defences: action parsing, injection sanitiser, data fencing, provenance gate,
grounding verifier."""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass

from .textutil import fold_text
from .tools import dumps

# ---------------------------------------------------------------- action parsing

@dataclass
class Parsed:
    kind: str                  # call | final | invalid
    tool: str = ""
    args: dict | None = None
    answer: str = ""
    error: str = ""


def _balanced(s: str) -> str:
    start = s.find("{")
    if start < 0:
        return ""
    s, depth, in_str, esc = s[start:], 0, None, False
    for i, ch in enumerate(s):
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == in_str: in_str = None
        elif ch in "\"'":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[: i + 1]
    return s + "}" * max(depth, 0)


def _load(blob: str, lenient: bool):
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        if not lenient:
            raise
    blob = re.sub(r",\s*([}\]])", r"\1", _balanced(blob))
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return ast.literal_eval(blob)


def parse_action(text: str, lenient: bool) -> Parsed:
    t = text.strip()
    if lenient:
        m = re.search(r"FINAL:\s*(.*)", t, re.S)
        if m and "ACTION:" not in t[: m.start()]:
            return Parsed("final", answer=m.group(1).strip())
        i = t.find("ACTION:")
        t = t[i:] if i >= 0 else t
    if t.startswith("FINAL:"):
        return Parsed("final", answer=t[6:].strip())
    if not t.startswith("ACTION:"):
        return Parsed("invalid", error="output must start with ACTION: or FINAL:")
    try:
        obj = _load(t[7:].strip(), lenient)
        if not (isinstance(obj, dict) and isinstance(obj.get("tool"), str) and isinstance(obj.get("args", {}), dict)):
            raise ValueError("bad shape")
        return Parsed("call", tool=obj["tool"], args=obj.get("args", {}))
    except (json.JSONDecodeError, ValueError, SyntaxError):
        return Parsed("invalid", error="ACTION payload is not valid JSON")

# ---------------------------------------------------------------- injection sanitiser

INJ_PATTERNS = [re.compile(p) for p in (
    r"ignore (all )?(previous|prior|above) instructions",
    r"^\s*(system|assistant|developer)\s*:",
    r"\[(admin|system)\]",
    r"send_email",
    r"\bsend (an )?e-?mail\b",
    r"final answer is",
    r"reply with final",
    r"new (policy|instructions?)",
)]
_SENT = re.compile(r"(?<=[.!?])\s+")


def _clean_text(s: str, normalize: bool) -> tuple[str, int]:
    keep, removed = [], 0
    for sent in _SENT.split(s):
        view = fold_text(sent) if normalize else sent.lower()
        if any(p.search(view) for p in INJ_PATTERNS):
            removed += 1
        else:
            keep.append(sent)
    return " ".join(keep), removed


def sanitize_obs(text: str, normalize: bool) -> tuple[str, int]:
    """Drop sentences that look like instructions inside string fields of a JSON observation."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return text, 0
    if not isinstance(obj, dict):
        return text, 0
    total = 0
    for k, v in list(obj.items()):
        if isinstance(v, str):
            obj[k], n = _clean_text(v, normalize)
            total += n
    return dumps(obj), total


def fence(text: str, tool: str) -> str:
    return f'<untrusted_data tool="{tool}">\n{text}\n</untrusted_data>'

# ---------------------------------------------------------------- provenance gate

def gate_check(tool: str, args: dict, user_prompt: str) -> tuple[bool, str]:
    """Side-effecting calls must be traceable to the user's own request."""
    if tool == "send_email":
        to = str(args.get("to", "")).lower()
        if not to or to not in user_prompt.lower():
            return False, "recipient was not specified by the user"
    return True, ""

# ---------------------------------------------------------------- grounding verifier

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def grounded(answer: str, evidence: list[str]) -> bool:
    a = answer.strip().rstrip(".").lower()
    if not a:
        return False
    blob = " ".join(evidence).lower()
    if a.replace(",", "") in blob.replace(",", ""):
        return True
    try:
        x = float(a.replace(",", ""))
    except ValueError:
        return False
    return any(abs(float(n) - x) <= 0.011 for n in _NUM.findall(blob))
