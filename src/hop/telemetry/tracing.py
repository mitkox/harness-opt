"""M2 local OpenTelemetry-compatible tracing (AOP-013).

Local/self-hosted only: no SaaS, no cloud exporter. The SDK here is stdlib:
W3C trace/span IDs, a run-rooted span tree, redacted attributes, and a
file exporter (run_dir/trace-otel.json) plus best-effort forwarding to a
local Collector directory. The OTel backend is a projection; the durable
trajectory ledger remains authoritative.

Span hierarchy (BUILD_PLAN §11; only real boundaries get spans):

  workflow.run
    admission.resolve
    workspace.prepare
    agent.execute
      context.prepare
      skill.select
      skill.load
      inference.request
      tool.execute
      context.compact
      subagent.execute
    output.freeze
    verifier.execute
    evaluation.record

Timing provenance (documented, not fabricated):
- measured: client-side span start/end (monotonic + wall clock);
- derived: durations computed from measured boundaries;
- unavailable: server prefill/decode, TTFT unless the harness/model server
  exposes them (recorded as null with reason, never synthesized).

Pinned convention: OTel GenAI semantic conventions are mapped through
GENAI_CONVENTION_REVISION; platform fields live under aop.*.
"""
from __future__ import annotations

import json
import os
import secrets
import time

from .redaction import sanitize_attributes

GENAI_CONVENTION_REVISION = "opentelemetry-semconv-incubating-10.0.0-genai-mapped"
GENAI_MAPPED_ATTRS = ("gen_ai.system", "gen_ai.request.model", "gen_ai.response.id",
                      "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
                      "gen_ai.response.finish_reasons")

SPANS = ("workflow.run", "admission.resolve", "workspace.prepare",
         "agent.execute", "context.prepare", "skill.select", "skill.load",
         "inference.request", "tool.execute", "context.compact",
         "subagent.execute", "output.freeze", "verifier.execute",
         "evaluation.record")


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


class Span:
    def __init__(self, name: str, trace_id: str, span_id: str,
                 parent_span_id: str = "", attributes: dict | None = None):
        assert name in SPANS, f"unknown span {name!r}"
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.attributes = sanitize_attributes(dict(attributes or {}))
        self.start_wall = time.time()
        self.start_mono = time.monotonic()
        self.end_wall: float | None = None
        self.end_mono: float | None = None
        self.status = "unset"

    def end(self, status: str = "ok") -> None:
        self.end_mono = time.monotonic()
        self.end_wall = time.time()
        self.status = status

    @property
    def duration_s(self) -> float | None:
        if self.end_mono is None:
            return None
        return self.end_mono - self.start_mono

    def to_dict(self) -> dict:
        return {
            "name": self.name, "trace_id": self.trace_id, "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_wall": self.start_wall, "end_wall": self.end_wall,
            "duration_s": self.duration_s, "status": self.status,
            "attributes": self.attributes,
            "provenance": {"timing": "measured-client-side",
                           "server_prefill_decode": "unavailable-unsynthesized",
                           "genai_convention": GENAI_CONVENTION_REVISION},
        }


class RunTracer:
    """One primary run trace per run (linked per-turn children optional)."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.trace_id = new_trace_id()
        self.spans: dict[str, Span] = {}
        self.stack: list[str] = []

    def start(self, name: str, attributes: dict | None = None) -> Span:
        parent = self.stack[-1] if self.stack else ""
        parent_span = self.spans[parent].span_id if parent else ""
        span = Span(name, self.trace_id, new_span_id(), parent_span, attributes)
        self.spans[name + ":" + span.span_id] = span
        self.stack.append(name + ":" + span.span_id)
        return span

    def current(self) -> Span | None:
        if not self.stack:
            return None
        return self.spans[self.stack[-1]]

    def finish(self, span: Span, status: str = "ok") -> None:
        span.end(status)
        key = next((k for k, v in self.spans.items() if v is span), None)
        if key and self.stack and self.stack[-1] == key:
            self.stack.pop()

    def export(self, path: str) -> dict:
        payload = {"run_id": self.run_id, "trace_id": self.trace_id,
                   "genai_convention": GENAI_CONVENTION_REVISION,
                   "spans": [s.to_dict() for s in self.spans.values()]}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".partial"
        with open(tmp, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return payload

    def span_for_event(self, event_name: str) -> Span | None:
        """Resolve the active span most relevant for a trajectory event."""
        mapping = {
            "run.admitted": "admission.resolve", "run.started": "workflow.run",
            "workspace.prepared": "workspace.prepare",
            "inference.request": "inference.request",
            "tool.request": "tool.execute", "tool.started": "tool.execute",
            "skill.selected": "skill.select", "skill.loaded": "skill.load",
            "verifier.started": "verifier.execute",
            "evaluation.recorded": "evaluation.record",
            "output.frozen": "output.freeze",
        }
        want = mapping.get(event_name)
        for span in self.spans.values():
            if span.name == want:
                return span
        return self.current()
