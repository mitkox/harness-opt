"""Pi headless-JSONL adapter, revision pi-json-adapter-v1 (AOP-009, ADR-002/005).

Wraps `pi -p --mode json` per attempt. Prompt acceptance (spawn + first
native event) is recorded as harness.accepted and is NEVER a completion
signal. Tool execution, cancellation, and session export work against an
isolated config/home; the user's global ~/.pi/agent state is untouched.

Local-only: the provider base URL, model id, context window, and generation
parameters derive from the registered ``ModelDeployment``. ``HOP_PI_BASE_URL``
(and the legacy ``AOP_PI_BASE_URL``) have no authority here and are scrubbed
from the worker environment.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time

from ..contracts.base import new_id
from ..contracts.harness import HarnessCapabilities, HarnessName
from ..contracts.model import ModelDeployment
from ..policy import assert_local_url, scrub_worker_env
from ..sandbox import SandboxLayout, SandboxSpec, spawn_isolated
from .base import HarnessOutput, PreparedSession

ADAPTER_REVISION = "pi-json-adapter-v1"
PROTOCOL = "pi-jsonl-v3"

# Native pi JSONL types carrying model-generated text (claims, not facts).
CLAIM_TYPES = {"message_update", "message_end"}

# Help flags that evidence the headless/streaming/model-selection contract.
REQUIRED_HELP_FLAGS = ("--print", "--mode", "--provider", "--model", "--approve",
                       "--session-dir", "--no-session")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def harness_executable_digest(executable: str) -> dict:
    """Identity of the exact executable/build behind a harness name."""
    path = shutil.which(executable) or executable
    real = os.path.realpath(path)
    evidence = {"requested": executable, "path": path, "realpath": real}
    if os.path.exists(real):
        evidence["sha256"] = _sha256_file(real)
        package_json = os.path.join(os.path.dirname(real), "package.json")
        for _ in range(6):
            if os.path.exists(package_json):
                break
            parent = os.path.dirname(os.path.dirname(package_json))
            if parent in ("/", ""):
                break
            package_json = os.path.join(parent, "package.json")
        if os.path.exists(package_json):
            evidence["package_json_sha256"] = _sha256_file(package_json)
            try:
                with open(package_json) as fh:
                    evidence["package_version"] = json.load(fh).get("version", "")
            except (OSError, json.JSONDecodeError):
                pass
    else:
        evidence["sha256"] = ""
    return evidence


class PiJsonAdapter:
    adapter_revision = ADAPTER_REVISION

    def __init__(self, deployment: ModelDeployment | None = None,
                 executable: str = "pi", provider: str = "hop_local",
                 spec: SandboxSpec | None = None):
        self.deployment = deployment
        self.executable = executable
        self.provider = provider
        self.spec = spec or SandboxSpec()
        self.model = ""
        self.base_url = ""
        if deployment is not None:
            if deployment.endpoint is None:
                raise ValueError("PiJsonAdapter requires a deployment with a registered endpoint")
            self.base_url = assert_local_url(deployment.endpoint.base_url)
            self.model = deployment.endpoint.model_id

    # -- capability probes -------------------------------------------------
    def probe(self) -> HarnessCapabilities:
        path = shutil.which(self.executable)
        evidence: dict = {}
        version = ""
        ok = False
        supports_headless = supports_streaming = supports_selection = False
        if path:
            try:
                vp = subprocess.run([path, "--version"], capture_output=True, text=True,
                                    timeout=15)
                version = (vp.stdout + vp.stderr).strip()[:200]
                evidence["version"] = {"exit_code": vp.returncode, "output": version}
                hp = subprocess.run([path, "--help"], capture_output=True, text=True,
                                    timeout=20)
                help_text = hp.stdout + hp.stderr
                flags = {flag: (flag in help_text) for flag in REQUIRED_HELP_FLAGS}
                evidence["help"] = {"exit_code": hp.returncode, "flags": flags,
                                    "help_sha256": hashlib.sha256(
                                        help_text.encode()).hexdigest()}
                supports_headless = bool(flags.get("--print") and flags.get("--mode"))
                supports_streaming = supports_headless and "--mode" in help_text
                supports_selection = bool(flags.get("--provider") and flags.get("--model"))
                ok = vp.returncode == 0 and supports_headless and supports_selection
            except (subprocess.TimeoutExpired, OSError) as exc:
                evidence["error"] = str(exc)
        exe = harness_executable_digest(self.executable)
        evidence["executable"] = exe
        probe_digest = hashlib.sha256(
            json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        return HarnessCapabilities(
            harness=HarnessName.PI, build_version=version or "unknown",
            adapter_revision=ADAPTER_REVISION,
            supports_headless=supports_headless,
            supports_streaming_events=supports_streaming,
            supports_cancellation=False,  # only a live termination probe may set this
            supports_session_export=supports_headless,
            supports_isolated_home=True, supports_model_selection=supports_selection,
            executable_path=exe.get("realpath", ""),
            executable_sha256=exe.get("sha256", ""),
            probe_evidence=evidence, probe_digest="sha256:" + probe_digest,
            unsupported={} if ok else {"all": "pi headless probe failed or executable missing"},
        )

    def probe_termination(self, timeout_s: float = 10.0) -> dict:
        """Actually spawn a headless run and confirm SIGTERM reaps the process group."""
        path = shutil.which(self.executable)
        if not path:
            return {"ok": False, "reason": "executable not found"}
        import signal
        proc = subprocess.Popen(
            [path, "-p", "--mode", "json", "--offline", "--no-session",
             "--provider", self.provider, "--model", self.model or "probe", "probe"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True, text=True)
        time.sleep(1.0)
        was_running = proc.poll() is None
        terminated = True
        if was_running:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                terminated = False
        evidence = {"ok": terminated, "was_running": was_running,
                    "exit_code": proc.returncode, "executable": path}
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
        return evidence

    # -- lifecycle ---------------------------------------------------------
    def prepare(self, bundle_digest: str, layout_root: str, identity: str) -> PreparedSession:
        """Write an isolated pi config (models + settings) under the run root."""
        if self.deployment is None:
            raise ValueError("cannot prepare Pi without a registered deployment")
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
        """Provider config is derived ONLY from the registered deployment record."""
        if self.deployment is None or self.deployment.endpoint is None:
            raise ValueError("PiJsonAdapter requires a deployment endpoint")
        # An injected HOP_PI_BASE_URL / AOP_PI_BASE_URL is deliberately ignored
        # and scrubbed.
        base = assert_local_url(self.deployment.endpoint.base_url)
        context = self.deployment.context_length_configured or 8192
        serving = self.deployment.serving_config or {}
        return {"providers": {self.provider: {
            "name": f"{self.provider} (local HOP)",
            "baseUrl": base, "api": "openai-completions", "apiKey": "local",
            "models": [{"id": self.model, "name": self.model,
                        "reasoning": bool(serving.get("reasoning", False)),
                        "input": ["text"], "contextWindow": context,
                        "maxTokens": int(serving.get("max_tokens", 8192))}]}}}

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
    """Map a native pi event to (event_type, authority).

    Harness/model output is a claim or a harness self-report; it is never a
    trusted platform observation merely because the harness produced it.
    """
    rtype = raw.get("type", "unknown")
    if rtype in CLAIM_TYPES:
        return f"harness.pi.{rtype}", "agent_claim"
    return f"harness.pi.{rtype}", "harness_observation"
