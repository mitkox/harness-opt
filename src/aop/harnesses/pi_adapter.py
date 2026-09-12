"""Pi headless-JSONL adapter, revision pi-json-adapter-v1 (AOP-009, ADR-002).

Wraps `pi -p --mode json` per attempt. Prompt acceptance (spawn + first
native event) is recorded as harness.accepted and is NEVER a completion
signal. Tool execution, cancellation, and session export work against an
isolated config/home; the user's global ~/.pi/agent state is untouched.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading

from ..contracts.base import new_id
from ..contracts.harness import HarnessCapabilities, HarnessName
from ..sandbox import SandboxLayout, SandboxSpec, spawn_isolated
from .base import HarnessOutput, PreparedSession

ADAPTER_REVISION = "pi-json-adapter-v1"
PROTOCOL = "pi-jsonl-v3"

# Native pi JSONL types carrying model-generated text (claims, not facts).
CLAIM_TYPES = {"message_update", "message_end"}


class PiJsonAdapter:
    adapter_revision = ADAPTER_REVISION

    def __init__(self, executable: str = "pi", provider: str = "mitko",
                 model: str = "mitko", spec: SandboxSpec | None = None):
        self.executable = executable
        self.provider = provider
        self.model = model
        self.spec = spec or SandboxSpec()

    def probe(self) -> HarnessCapabilities:
        path = shutil.which(self.executable)
        version = ""
        ok = False
        if path:
            try:
                proc = subprocess.run([path, "--version"], capture_output=True,
                                      text=True, timeout=15)
                version = (proc.stdout + proc.stderr).strip()[:200]
                ok = proc.returncode == 0
            except (subprocess.TimeoutExpired, OSError):
                ok = False
        return HarnessCapabilities(
            harness=HarnessName.PI, build_version=version or "unknown",
            adapter_revision=ADAPTER_REVISION,
            supports_headless=ok, supports_streaming_events=ok,
            supports_cancellation=ok, supports_session_export=ok,
            supports_isolated_home=True, supports_model_selection=ok,
            unsupported={} if ok else {"all": "pi executable unavailable"},
        )

    def prepare(self, bundle_digest: str, layout_root: str, identity: str) -> PreparedSession:
        """Write an isolated pi config (models + settings) under the run root."""
        config_dir = os.path.join(layout_root, "pi-config")
        session_dir = os.path.join(layout_root, "session")
        os.makedirs(config_dir, exist_ok=True)
        os.makedirs(session_dir, exist_ok=True)
        with open(os.path.join(config_dir, "models.json"), "w") as fh:
            json.dump(self._local_models_config(), fh, indent=2)
        with open(os.path.join(config_dir, "settings.json"), "w") as fh:
            json.dump({
                "defaultProvider": self.provider, "defaultModel": self.model,
                "quietStartup": True, "enableInstallTelemetry": False,
            }, fh, indent=2)
        return PreparedSession(
            session_id=new_id("pi"), harness="pi", layout_root=layout_root,
            extra_env={
                "PI_CODING_AGENT_DIR": config_dir,
                "PI_CODING_AGENT_SESSION_DIR": session_dir,
                "PI_OFFLINE": "1",
            })

    def _local_models_config(self) -> dict:
        base = os.environ.get("AOP_PI_BASE_URL", "http://127.0.0.1:8000/v1")
        return {"providers": {self.provider: {
            "name": f"{self.provider} (local AOP)",
            "baseUrl": base, "api": "openai-completions", "apiKey": "local",
            "models": [{"id": self.model, "name": self.model, "reasoning": True,
                        "input": ["text"], "contextWindow": 262144, "maxTokens": 8192}]}}}

    def start(self, task_prompt: str, session: PreparedSession, workspace: str,
              stdout_path: str, stderr_path: str, timeout_s: float,
              cancel: threading.Event, on_native_event=None) -> HarnessOutput:
        layout = SandboxLayout(root=session.layout_root, workspace=workspace,
                               home=os.path.join(session.layout_root, "home"),
                               cache=os.path.join(session.layout_root, "cache"),
                               session=os.path.join(session.layout_root, "session"),
                               tmp=os.path.join(session.layout_root, "tmp"))
        for path in (layout.home, layout.cache, layout.tmp):
            os.makedirs(path, exist_ok=True)
        cmd = [self.executable, "-p", "--mode", "json",
               "--provider", self.provider, "--model", self.model,
               "--session-dir", os.path.join(session.layout_root, "session"),
               "--approve", task_prompt]
        result = spawn_isolated(cmd, layout, self.spec, extra_env=session.extra_env,
                                timeout_s=timeout_s, cancel=cancel,
                                stdout_path=stdout_path, stderr_path=stderr_path)
        if on_native_event is not None:
            for raw in _read_jsonl(stdout_path):
                on_native_event(raw)
        if result.cancelled:
            status = "cancelled"
        elif result.timed_out:
            status = "timeout"
        elif result.exit_code == 0:
            status = "exited"
        else:
            status = "crashed"
        return HarnessOutput(terminal_status=status, exit_code=result.exit_code,
                             agent_claim=_extract_last_text(stdout_path))


def _read_jsonl(path: str):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _extract_last_text(stdout_path: str) -> str:
    """Agent-reported narration only. Never a verdict (AOP-009 acceptance)."""
    last = ""
    for raw in _read_jsonl(stdout_path):
        msg = raw.get("message") or {}
        content = msg.get("content") or []
        texts = [c.get("text", "") for c in content
                 if isinstance(c, dict) and c.get("type") == "text"
                 and msg.get("role") == "assistant"]
        if texts:
            last = "\n".join(texts)
        for upd in [raw.get("assistantMessageEvent") or {}]:
            if upd.get("type") == "text_delta" and upd.get("delta"):
                last += upd["delta"]
    return last[-4000:]


def native_to_trajectory_kind(raw: dict) -> tuple[str, str]:
    """Map a native pi event to (event_type, authority)."""
    rtype = raw.get("type", "unknown")
    if rtype in CLAIM_TYPES:
        return f"harness.pi.{rtype}", "agent_report"
    return f"harness.pi.{rtype}", "trusted_observer"
