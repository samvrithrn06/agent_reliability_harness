"""ToolRunner: what agents call. Wraps the fault injector, counts calls, writes trace events."""
from __future__ import annotations

from .faults import FaultInjector, ToolResult
from .tools import Env
from .trace import Tracer


class ToolRunner:
    def __init__(self, env: Env, injector: FaultInjector, tracer: Tracer):
        self.env, self.injector, self.tracer = env, injector, tracer
        self.calls = 0
        self.fault_log: list[dict] = []

    def call(self, tool: str, args: dict) -> ToolResult:
        self.calls += 1
        res = self.injector.call(tool, args, self.env)
        self.fault_log.extend(res.faults)
        self.tracer.event("tool_call", tool=tool, args=args, faults=res.fault_types, text=res.text)
        return res
