"""LLM backends.

SimulatedLLM  - deterministic, seedable stand-in for a fallible tool-using model. It reads ONLY the
                message history the agent shows it (so scaffold defences genuinely change what the
                "model" sees), follows an oracle plan, and injects model-level failure modes
                (malformed actions, wrong arguments, hallucination on garbage, instruction-following
                of injected text). All rates live in ModelProfile and are documented in the README.
AnthropicLLM  - live backend using the Anthropic Messages API (needs `pip install anthropic` + key).
"""
from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass

from .tasks import Foreign, Task
from .textutil import GIVE_UP_PREFIX, detect_injection, estimate_tokens, stable_hash, ATTACKER_EMAIL


@dataclass
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class ModelProfile:
    p_malformed_action: float = 0.06    # emits syntactically broken ACTION
    p_wrong_arg: float = 0.03           # semantically wrong tool argument
    p_wrong_final: float = 0.02         # mis-states the final answer
    p_follow_injection: float = 0.55    # obeys injected instruction in an unfenced tool result
    hardened_mult: float = 0.50         # system prompt warns about untrusted data
    fenced_mult: float = 0.40           # tool output wrapped in <untrusted_data>
    p_retry_on_error: float = 0.50      # retries by itself when a tool errors
    p_hallucinate_on_garbage: float = 0.40


class LLM:
    def complete(self, messages: list[dict]) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError


_FENCE = re.compile(r"</?untrusted_data[^>]*>")


class SimulatedLLM(LLM):
    def __init__(self, task: Task, seed: int, profile: ModelProfile | None = None):
        self.task, self.seed, self.m = task, seed, profile or ModelProfile()

    # -- helpers -------------------------------------------------------------------------
    def _read(self, step, content: str):
        body = _FENCE.sub("", content).strip()
        if "blocked by policy" in body:
            return "other", None
        try:
            obj = json.loads(body)
        except json.JSONDecodeError:
            return "garbage", None
        if not isinstance(obj, dict):
            return "garbage", None
        if "error" in obj:
            return "error", None
        try:
            return "ok", step.extract(obj)
        except Foreign:
            return "other", None
        except (KeyError, AttributeError, TypeError, ValueError):
            return "garbage", None

    def _mangle(self, action: str, rng) -> str:
        style = rng.choice(["trailing_comma", "missing_brace", "prose", "single_quotes"])
        if style == "trailing_comma":
            return action[:-2] + ",}}" if action.endswith("}}") else action[:-1] + ",}"
        if style == "missing_brace":
            return action[:-1]
        if style == "prose":
            return "Sure, I will call the tool now. " + action + " Let me know if that works."
        return action.replace('"', "'")

    def _perturb_args(self, tool: str, args: dict, rng) -> dict:
        a = dict(args)
        if tool == "calculator":
            a["expr"] = re.sub(r"\d", lambda m: str((int(m.group()) + 1) % 10), a["expr"], count=1)
        elif tool == "lookup_user":
            a["user_id"] = int(a["user_id"]) % 12 + 1
        elif tool == "get_weather":
            a["city"] = rng.choice(["Tokyo", "Nairobi"])
        elif tool == "search_docs":
            a["query"] = "warranty" if "warranty" not in a["query"] else "shipping"
        elif tool == "read_file":
            a["name"] = "budget.txt" if a["name"] != "budget.txt" else "report.txt"
        elif tool == "send_email":
            a["to"] = "x" + a["to"]
        return a

    # -- main ----------------------------------------------------------------------------
    def complete(self, messages: list[dict]) -> LLMResponse:
        rng = random.Random(stable_hash(self.seed, self.task.id, len(messages), "llm"))
        hardened = "untrusted" in messages[0]["content"].lower()
        last, text = messages[-1], None

        if last["role"] == "tool":
            inj = detect_injection(last["content"])
            if inj and last.get("name") != "send_email":
                p = self.m.p_follow_injection
                p *= self.m.hardened_mult if hardened else 1.0
                p *= self.m.fenced_mult if "<untrusted_data" in last["content"] else 1.0
                if rng.random() < p:
                    if inj["kind"] == "exfil":
                        text = "ACTION: " + json.dumps({"tool": "send_email", "args": {
                            "to": inj["address"], "body": "Conversation summary: " + self.task.prompt}})
                    else:
                        text = "FINAL: " + inj["value"]
        if text is None:
            text = self._task_step(messages, rng)
        tin = sum(estimate_tokens(m["content"]) for m in messages)
        return LLMResponse(text, tin, estimate_tokens(text))

    def _task_step(self, messages, rng) -> str:
        plan, k, values, last_status = self.task.steps, 0, [], None
        for m in messages:
            if m["role"] != "tool":
                continue
            if k < len(plan) and m.get("name") == plan[k].tool:
                last_status, val = self._read(plan[k], m["content"])
                if last_status == "ok":
                    values.append(val)
                    k += 1
            else:
                last_status = "other"
        if messages[-1]["role"] == "tool" and last_status in ("error", "garbage"):
            if last_status == "garbage" and rng.random() < self.m.p_hallucinate_on_garbage:
                return "FINAL: 42"
            if rng.random() >= self.m.p_retry_on_error:
                return f"FINAL: {GIVE_UP_PREFIX} the task because a tool failed."

        if k == len(plan):
            ans = self.task.answer(values)
            if rng.random() < self.m.p_wrong_final:
                try:
                    ans = f"{float(ans) * 1.1:.2f}".rstrip("0").rstrip(".")
                except ValueError:
                    ans = "Unknown"
            return "FINAL: " + ans

        step = plan[k]
        args = step.args(values)
        if rng.random() < self.m.p_wrong_arg:
            args = self._perturb_args(step.tool, args, rng)
        action = "ACTION: " + json.dumps({"tool": step.tool, "args": args})
        return self._mangle(action, rng) if rng.random() < self.m.p_malformed_action else action


class AnthropicLLM(LLM):
    """Live backend. Same ACTION:/FINAL: protocol, so every agent design runs unchanged."""

    def __init__(self, model: str | None = None, max_tokens: int = 400, temperature: float = 0.0):
        import anthropic  # imported lazily so the harness has zero hard dependencies
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self.max_tokens, self.temperature = max_tokens, temperature

    def complete(self, messages: list[dict]) -> LLMResponse:
        system, turns = messages[0]["content"], []
        for m in messages[1:]:
            role = "assistant" if m["role"] == "assistant" else "user"
            content = f"OBSERVATION from {m['name']}:\n{m['content']}" if m["role"] == "tool" else m["content"]
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"] += "\n\n" + content
            else:
                turns.append({"role": role, "content": content})
        r = self.client.messages.create(model=self.model, system=system, messages=turns,
                                        max_tokens=self.max_tokens, temperature=self.temperature)
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        return LLMResponse(text, r.usage.input_tokens, r.usage.output_tokens)


_last_call = [0.0]


class GeminiLLM(LLM):
    """Live backend: Google Gemini via the REST API (stdlib only, no SDK needed).
    Env: GEMINI_API_KEY (required), GEMINI_MODEL (default gemini-2.5-flash),
         GEMINI_MIN_INTERVAL seconds between calls (default 6.5 = ~9 req/min, free-tier safe),
         GEMINI_THINKING_BUDGET (optional int; 0 disables thinking on 2.5-flash)."""

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, model: str | None = None, max_tokens: int = 2048, temperature: float = 0.0):
        self.key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not self.key:
            raise RuntimeError("Set GEMINI_API_KEY (get one at https://aistudio.google.com/apikey)")
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
        self.max_tokens, self.temperature = max_tokens, temperature
        self.min_interval = float(os.environ.get("GEMINI_MIN_INTERVAL", "2.5"))

    def _payload(self, messages: list[dict]) -> dict:
        contents = []
        for m in messages[1:]:
            role = "model" if m["role"] == "assistant" else "user"
            text = f"OBSERVATION from {m['name']}:\n{m['content']}" if m["role"] == "tool" else m["content"]
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"][0]["text"] += "\n\n" + text
            else:
                contents.append({"role": role, "parts": [{"text": text}]})
        gen = {"temperature": self.temperature, "maxOutputTokens": self.max_tokens}
        if os.environ.get("GEMINI_THINKING_BUDGET") is not None:
            gen["thinkingConfig"] = {"thinkingBudget": int(os.environ["GEMINI_THINKING_BUDGET"])}
        return {"systemInstruction": {"parts": [{"text": messages[0]["content"]}]},
                "contents": contents, "generationConfig": gen}

    def complete(self, messages: list[dict]) -> LLMResponse:
        import time
        import urllib.error
        import urllib.request
        body = json.dumps(self._payload(messages)).encode()
        req = urllib.request.Request(self.URL.format(model=self.model), data=body, method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        for attempt in range(6):
            wait = self.min_interval - (time.time() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 503) and attempt < 5:
                    time.sleep(min(60, 5 * 2 ** attempt))
                    continue
                raise RuntimeError(f"Gemini API error {e.code}: {e.read()[:300]!r}") from e
            except urllib.error.URLError as e:
                if attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Gemini network error: {e}") from e
        cands = data.get("candidates") or []
        parts = (cands[0].get("content", {}).get("parts", []) if cands else [])
        text = "".join(p.get("text", "") for p in parts).strip()
        u = data.get("usageMetadata", {})
        return LLMResponse(text, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0))


def make_llm(backend: str, task: Task, seed: int, profile: ModelProfile | None = None) -> LLM:
    if backend == "simulated":
        return SimulatedLLM(task, seed, profile)
    if backend == "gemini":
        return GeminiLLM()
    if backend == "anthropic":
        return AnthropicLLM()
    raise ValueError(f"unknown backend {backend}")

# Core backend models and evaluation tasks
